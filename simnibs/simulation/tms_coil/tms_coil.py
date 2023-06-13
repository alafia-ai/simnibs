import json
import re
from typing import Optional

import jsonschema
import nibabel as nib
import numpy as np
import numpy.typing as npt
import scipy.optimize as opt

from simnibs import __version__
from simnibs.mesh_tools import mesh_io
from simnibs.mesh_tools.mesh_io import Msh, NodeData
from simnibs.simulation.tms_coil.tcd_element import TcdElement
from simnibs.simulation.tms_coil.tms_coil_deformation import TmsCoilDeformation
from simnibs.simulation.tms_coil.tms_coil_element import (
    DipoleElements,
    SampledGridPointElements,
    TmsCoilElements,
)
from simnibs.simulation.tms_coil.tms_coil_model import TmsCoilModel
from simnibs.simulation.tms_coil.tms_stimulator import TmsStimulator, TmsWaveform
from simnibs.utils import file_finder


class TmsCoil(TcdElement):
    """A representation of a coil used for TMS

    Parameters
    ----------
    name : Optional[str]
        The name of the coil
    brand : Optional[str]
        The brand of the coil
    version : Optional[str]
        The version of the coil
    limits : Optional[npt.NDArray[np.float_]] (3x2)
        Used for expansion into NIfTI digitized files.
        This is in mm and follows the structure [[min(x), max(x)],[min(y), max(y)], [min(z), max(z)]]
    resolution : Optional[npt.NDArray[np.float_]] (3)
        The sampling resolution (step width in mm) for expansion into NIfTI files.
        This follows the structure [rx,ry,rz]
    casing : Optional[TmsCoilModel]
        The casing of the coil
    elements : list[TmsCoilElement]
        The stimulation elements of the coil

    Attributes
    ----------------------
    name : Optional[str]
        The name of the coil
    brand : Optional[str]
        The brand of the coil
    version : Optional[str]
        The version of the coil
    limits : Optional[npt.NDArray[np.float_]] (3x2)
        Used for expansion into NIfTI digitized files.
        This is in mm and follows the structure [[min(x), max(x)],[min(y), max(y)], [min(z), max(z)]]
    resolution : Optional[npt.NDArray[np.float_]] (3)
        The sampling resolution (step width in mm) for expansion into NIfTI files.
        This follows the structure [rx,ry,rz]
    casing : Optional[TmsCoilModel]
        The casing of the coil
    elements : list[TmsCoilElement]
        The stimulation elements of the coil
    deformations : list[TmsCoilDeformation]
        All deformations used in the stimulation elements of the coil
    """

    def __init__(
        self,
        name: Optional[str],
        brand: Optional[str],
        version: Optional[str],
        limits: Optional[npt.NDArray[np.float_]],
        resolution: Optional[npt.NDArray[np.float_]],
        casing: Optional[TmsCoilModel],
        elements: list[TmsCoilElements],
    ):
        self.name = name
        self.brand = brand
        self.version = version
        self.limits = limits
        self.resolution = resolution
        self.casing = casing
        self.elements = elements

        self.deformations: list[TmsCoilDeformation] = []
        for coil_element in self.elements:
            for coil_deformation in coil_element.deformations:
                if coil_deformation not in self.deformations:
                    self.deformations.append(coil_deformation)

    def get_da_dt(
        self,
        msh: Msh,
        coil_affine: npt.NDArray[np.float_],
        di_dt: float,
        eps: float = 1e-3,
    ) -> NodeData:
        """Calculate the dA/dt field applied by the coil at each node of the mesh.

        Parameters
        ----------
        msh : Msh
            The mesh at which nodes the dA/dt field should be calculated
        coil_affine : npt.NDArray[np.float_]
            The affine transformation that is applied to the coil
        di_dt : float
            dI/dt in A/s
        eps : float, optional
            The requested precision, by default 1e-3

        Returns
        -------
        NodeData
            The dA/dt field at every node of the mesh
        """
        target_positions = msh.nodes.node_coord
        A = np.zeros_like(target_positions)
        for coil_element in self.elements:
            A += coil_element.get_da_dt(target_positions, coil_affine, di_dt, eps)

        return NodeData(A)

    def get_a_field(
        self,
        points: npt.NDArray[np.float_],
        coil_affine: npt.NDArray[np.float_],
        eps: float = 1e-3,
    ) -> npt.NDArray[np.float_]:
        """Calculates the A field applied by the coil at each point.

        Parameters
        ----------
        points : npt.NDArray[np.float_]
            The points at which the A field should be calculated (in mm)
        coil_affine : npt.NDArray[np.float_]
            The affine transformation that is applied to the coil
        eps : float, optional
            The requested precision, by default 1e-3

        Returns
        -------
        npt.NDArray[np.float_]
            The A field at every point
        """
        a_field = np.zeros_like(points)
        for coil_element in self.elements:
            a_field += coil_element.get_a_field(points, coil_affine, eps)

        return a_field

    def get_mesh(
        self,
        coil_affine: Optional[npt.NDArray[np.float_]] = None,
        apply_deformation: bool = True,
        include_casing: bool = True,
        include_optimization_points: bool = True,
        include_coil_elements: bool = True,
    ) -> Msh:
        """Generates a mesh of the coil

        Parameters
        ----------
        coil_affine : Optional[npt.NDArray[np.float_]], optional
            The affine transformation that is applied to the coil, by default None
        apply_deformation : bool, optional
            Whether or not to apply the current coil element deformations, by default True
        include_casing : bool, optional
            Whether or not to include the casing mesh, by default True
        include_optimization_points : bool, optional
            Whether or not to include the min distance and intersection points, by default True
        include_coil_elements : bool, optional
            Whether or not to include the stimulating elements in the mesh, by default True

        Returns
        -------
        Msh
            The generated mesh of the coil
        """
        if coil_affine is None:
            coil_affine = np.eye(4)

        coil_msh = Msh()
        if self.casing is not None:
            coil_msh = coil_msh.join_mesh(
                self.casing.get_mesh(
                    coil_affine, include_casing, include_optimization_points, 0
                )
            )
        for i, coil_element in enumerate(self.elements):
            coil_msh = coil_msh.join_mesh(
                coil_element.get_mesh(
                    coil_affine,
                    apply_deformation,
                    include_casing,
                    include_optimization_points,
                    include_coil_elements,
                    (i + 1) * 100,
                )
            )
        return coil_msh

    def get_casing_coordinates(
        self,
        affine: Optional[npt.NDArray[np.float_]] = None,
        apply_deformation: bool = True,
    ) -> tuple[npt.NDArray[np.float_], npt.NDArray[np.float_], npt.NDArray[np.float_]]:
        """Returns all casing points, min distance points and intersect points of this coil and the coil elements.

        Parameters
        ----------
        affine : Optional[npt.NDArray[np.float_]], optional
            The affine transformation that is applied to the coil, by default None
        apply_deformation : bool, optional
            Whether or not to apply the current coil element deformations, by default True

        Returns
        -------
        tuple[npt.NDArray[np.float_], npt.NDArray[np.float_], npt.NDArray[np.float_]]
            A tuple containing the casing points, min distance points and intersect points
        """
        if affine is None:
            affine = np.eye(4)

        casing_points = (
            [self.casing.get_points(affine)] if self.casing is not None else []
        )
        min_distance_points = (
            [self.casing.get_min_distance_points(affine)]
            if self.casing is not None
            else []
        )
        intersect_points = (
            [self.casing.get_intersect_points(affine)]
            if self.casing is not None
            else []
        )

        for coil_element in self.elements:
            if coil_element.casing is not None:
                element_casing_points = coil_element.get_casing_coordinates(
                    affine, apply_deformation
                )
                casing_points.append(element_casing_points[0])
                min_distance_points.append(element_casing_points[1])
                intersect_points.append(element_casing_points[2])

        casing_points = np.concatenate(casing_points, axis=0)
        min_distance_points = np.concatenate(min_distance_points, axis=0)
        intersect_points = np.concatenate(intersect_points, axis=0)

        return casing_points, min_distance_points, intersect_points

    @staticmethod
    def _add_logo(mesh: Msh) -> Msh:
        """Adds the SimNIBS logo to the coil surface

        Parameters
        ----------
        mesh : Msh
            The mesh of the coil

        Returns
        -------
        Msh
            The coil mesh including the SimNIBS logo
        """

        msh_logo = Msh(fn=file_finder.templates.simnibs_logo)

        # 'simnibs' has tag 1, '3' has tag 2, '4' has tag 3
        # renumber tags, because they will be converted to color:
        # 0 gray, 1 red, 2 lightblue, 3 blue
        major_version = __version__.split(".")[0]
        if major_version == "3":
            msh_logo = msh_logo.crop_mesh(tags=[1, 2])
            msh_logo.elm.tag1[msh_logo.elm.tag1 == 2] = 3  # version in blue
        elif major_version == "4":
            msh_logo = msh_logo.crop_mesh(tags=[1, 3])
        else:
            msh_logo = msh_logo.crop_mesh(tags=1)
        msh_logo.elm.tag1[msh_logo.elm.tag1 == 1] = 2  # 'simnibs' in light blue

        # center logo in xy-plane, mirror at yz-plane and scale
        bbox_coil = np.vstack([np.min(mesh.nodes[:], 0), np.max(mesh.nodes[:], 0)])
        bbox_logo = np.vstack(
            [np.min(msh_logo.nodes[:], 0), np.max(msh_logo.nodes[:], 0)]
        )
        bbox_ratio = np.squeeze(np.diff(bbox_logo, axis=0) / np.diff(bbox_coil, axis=0))
        bbox_ratio = max(bbox_ratio[0:2])  # maximal size ratio in xy plane

        msh_logo.nodes.node_coord[:, 0:2] -= np.mean(bbox_logo[:, 0:2], axis=0)
        msh_logo.nodes.node_coord[:, 0] = -msh_logo.nodes.node_coord[:, 0]
        msh_logo.nodes.node_coord[:, 0:2] *= 1 / (4 * bbox_ratio)

        # shift logo along negative z to the top side of coil
        msh_logo.nodes.node_coord[:, 2] += bbox_coil[0, 2] - bbox_logo[0, 2] - 5

        mesh = mesh.join_mesh(msh_logo)
        return mesh

    @classmethod
    def from_file(cls, fn: str):
        """Loads the coil file. The file has to be either in the tcd, ccd or the NIfTI format

        Parameters
        ----------
        fn : str
            The path to the coil file

        Returns
        -------
        TmsCoil
            The tms coil loaded from the coil file

        Raises
        ------
        IOError
            If the file type is unsupported or the file extension for a NIfTI file is missing
        """
        if fn.endswith(".tcd"):
            return TmsCoil.from_tcd(fn)
        elif fn.endswith(".ccd"):
            return TmsCoil.from_ccd(fn)
        elif fn.endswith(".nii.gz") or fn.endswith(".nii"):
            return TmsCoil.from_nifti(fn)

        try:
            return TmsCoil.from_tcd(fn)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        try:
            return TmsCoil.from_ccd(fn)
        except (UnicodeDecodeError, ValueError):
            pass

        raise IOError(
            "Error loading file: Unsupported file type or missing file extension for NIfTI file"
        )

    def write(self, fn: str):
        """Writes the TMS coil in the tcd format

        Parameters
        ----------
        fn : str
            The path and file name to store the tcd coil file as
        """
        self.write_tcd(fn)

    @classmethod
    def from_ccd(
        cls,
        fn: str,
        fn_coil_casing: Optional[str] = None,
        fn_waveform_file: Optional[str] = None,
    ):
        """Loads a ccd coil file with the optional addition of a coil casing as an stl file and waveform information from a tsv file

        Parameters
        ----------
        fn : str
            The path to the ccd coil file
        fn_coil_casing : Optional[str], optional
            The path to a stl coil casing file, by default None
        fn_waveform_file : Optional[str], optional
            the path to a tsv waveform information file, by default None

        Returns
        -------
        TmsCoil
            The coil loaded from the ccd (and optional stl and tsv) file
        """
        with open(fn, "r") as f:
            header = f.readline()

        coil_casing = None
        if fn_coil_casing is not None:
            coil_casing_mesh = mesh_io.read_stl(fn_coil_casing)
            coil_casing = TmsCoilModel(coil_casing_mesh, None, None)

        waveforms = None
        if fn_waveform_file is not None:
            waveform_data = np.genfromtxt(
                fn_waveform_file, delimiter="\t", filling_values=0, names=True
            )
            names = waveform_data.dtype.names
            waveforms = [
                TmsWaveform(
                    names[1],
                    waveform_data[names[0]],
                    waveform_data[names[1]],
                    waveform_data[names[2]],
                )
            ]

        meta_informations = header.replace("\n", "").split(";")
        file_discription = meta_informations[0]
        version_match = re.search(r"version (\d+\.\d+)", file_discription)
        file_version = version_match.group(1) if version_match else None

        parametric_information = meta_informations[1:]
        parametric_information = [pair.strip() for pair in parametric_information]
        parametric_information = [
            pair for pair in parametric_information if len(pair) > 0
        ]

        header_dict = {}
        for pair in parametric_information:
            key, value = pair.split("=")

            if value == "none":
                value = None
            elif "." in value:
                value = float(value)
            elif value.isdigit():
                value = int(value)
            elif "," in value:
                value = np.fromstring(value, dtype=int, sep=",")

            header_dict[key] = value

        bb = []
        for dim in ("x", "y", "z"):
            a = header_dict.get(dim)
            if a is None:
                bb.append(None)
            else:
                if len(a) < 2:
                    bb.append((-np.abs(a[0]), np.abs(a[0])))
                else:
                    bb.append(a)

        res = []
        a = header_dict.get("resolution")
        if a is None:
            res.append(None)
        else:
            a = np.atleast_1d(a)
            if len(a) < 3:
                for i in range(len(a), 3):
                    a = np.concatenate((a, (a[i - 1],)))
            res = a

        ccd_file = np.atleast_2d(np.loadtxt(fn, skiprows=2))

        dipole_positions = ccd_file[:, 0:3] * 1e3
        dipole_moments = ccd_file[:, 3:]

        stimulator = None
        if "dIdtmax" in header_dict.keys():
            stimulator = TmsStimulator(
                header_dict.get("stimulator"), None, header_dict["dIdtmax"], waveforms
            )

        coil_elements = [
            DipoleElements(None, None, [], dipole_positions, dipole_moments, stimulator)
        ]

        return cls(
            header_dict.get("coilname"),
            header_dict.get("brand"),
            file_version,
            np.array(bb),
            np.array(res),
            coil_casing,
            coil_elements,
        )

    def to_tcd(self) -> dict:
        """Packs the coil information into a tcd like dictionary

        Returns
        -------
        dict
            A tcd like dictionary representing the coil
        """
        tcd_coil_models = []
        coil_models = []
        if self.casing is not None:
            tcd_coil_models.append(self.casing.to_tcd())
            coil_models.append(self.casing)

        tcd_deforms = []

        tcd_stimulators = []
        stimulators = []

        for deformation in self.deformations:
            tcd_deforms.append(deformation.to_tcd())

        tcd_coil_elements = []
        for coil_element in self.elements:
            if (
                coil_element.casing not in coil_models
                and coil_element.casing is not None
            ):
                coil_models.append(coil_element.casing)
                tcd_coil_models.append(coil_element.casing.to_tcd())

            if (
                coil_element.stimulator not in stimulators
                and coil_element.stimulator is not None
            ):
                stimulators.append(coil_element.stimulator)
                tcd_stimulators.append(coil_element.stimulator.to_tcd())

            tcd_coil_elements.append(
                coil_element.to_tcd(stimulators, coil_models, self.deformations)
            )

        tcd_coil = {}
        if self.name is not None:
            tcd_coil["name"] = self.name
        if self.brand is not None:
            tcd_coil["brand"] = self.brand
        if self.version is not None:
            tcd_coil["version"] = self.version
        if self.limits is not None:
            tcd_coil["limits"] = self.limits.tolist()
        if self.resolution is not None:
            tcd_coil["resolution"] = self.resolution.tolist()
        if self.casing is not None:
            tcd_coil["coilCasing"] = coil_models.index(self.casing)
        tcd_coil["coilElementList"] = tcd_coil_elements
        if len(tcd_stimulators) > 0:
            tcd_coil["stimulatorList"] = tcd_stimulators
        if len(tcd_deforms) > 0:
            tcd_coil["deformList"] = tcd_deforms
        if len(tcd_coil_models) > 0:
            tcd_coil["coilModels"] = tcd_coil_models

        return tcd_coil

    @classmethod
    def from_tcd_dict(cls, coil: dict, validate=True):
        """Loads the coil from a tcd like dictionary

        Parameters
        ----------
        coil : dict
            A tcd like dictionary storing coil information
        validate : bool, optional
            Whether or not to validate the dictionary based on the tcd coil json schema, by default True

        Returns
        -------
        TmsCoil
            The TMS coil loaded from the tcd like dictionary

        Raises
        ------
        ValidationError
            Raised if validate is true and the dictionary is not valid to the tcd coil json schema
        """
        if validate:
            with open(file_finder.templates.tcd_json_schema, "r") as fid:
                tcd_schema = json.loads(fid.read())

            try:
                jsonschema.validate(coil, tcd_schema)
            except jsonschema.ValidationError as e:
                instance = str(e.instance)
                e.instance = (
                    instance
                    if len(instance) < 900
                    else f"{instance[:400]} ... {instance[-400:]}"
                )
                raise e

        coil_models = []
        for coil_model in coil.get("coilModels", []):
            coil_models.append(TmsCoilModel.from_tcd_dict(coil_model))

        deformations = []
        for deform in coil.get("deformList", []):
            deformations.append(TmsCoilDeformation.from_tcd(deform))

        stimulators = []
        for stimulator in coil.get("stimulatorList", []):
            stimulators.append(TmsStimulator.from_tcd(stimulator))

        coil_elements = []
        for coil_element in coil["coilElementList"]:
            coil_elements.append(
                TmsCoilElements.from_tcd_dict(
                    coil_element, stimulators, coil_models, deformations
                )
            )

        coil_casing = (
            None if coil.get("coilCasing") is None else coil_models[coil["coilCasing"]]
        )

        return cls(
            coil.get("name"),
            coil.get("brand"),
            coil.get("version"),
            None if coil.get("limits") is None else np.array(coil["limits"]),
            None if coil.get("resolution") is None else np.array(coil["resolution"]),
            coil_casing,
            coil_elements,
        )

    @classmethod
    def from_tcd(cls, fn: str, validate=True):
        """Loads the TMS coil from a tcd file

        Parameters
        ----------
        fn : str
            The path to the ccd coil file
        validate : bool, optional
            Whether or not to validate the dictionary based on the tcd coil json schema, by default True

        Returns
        -------
        TmsCoil
            The TMS coil loaded from the tcd file
        """
        with open(fn, "r") as fid:
            coil = json.loads(fid.read())

        return cls.from_tcd_dict(coil, validate)

    def write_tcd(self, fn: str):
        """Writes the coil as a tcd file

        Parameters
        ----------
        fn : str
            The path and file name to store the tcd coil file as
        """
        with open(fn, "w") as json_file:
            json.dump(self.to_tcd(), json_file, indent=4)

    @classmethod
    def from_nifti(cls, fn: str):
        """Loads coil information from a NIfTI file

        Parameters
        ----------
        fn : str
            The path to the coil NIfTI file

        Returns
        -------
        TmsCoil
            The TMS coil loaded from the NIfTI file
        """
        nifti = nib.load(fn)
        data = nifti.get_fdata()
        affine = nifti.affine

        resolution = np.array(
            [
                affine[0][0],
                affine[1][1],
                affine[2][2],
            ]
        )

        limits = np.array(
            [
                [affine[0][3], data.shape[0] * resolution[0] + affine[0][3]],
                [affine[1][3], data.shape[1] * resolution[1] + affine[1][3]],
                [affine[2][3], data.shape[2] * resolution[2] + affine[2][3]],
            ]
        )

        coil_elements = [SampledGridPointElements(None, None, [], data, affine, None)]

        return cls(None, None, None, limits, resolution, None, coil_elements)

    def write_nifti(
        self,
        fn: str,
        limits: Optional[npt.NDArray[np.float_]] = None,
        resolution: Optional[npt.NDArray[np.float_]] = None,
    ):
        """Writes the A field of the coil in the NIfTI file format

        Parameters
        ----------
        fn : str
           The path and file name to store the tcd coil file as
        limits : Optional[npt.NDArray[np.float_]], optional
            Overrides the limits set in the coil object, by default None
        resolution : Optional[npt.NDArray[np.float_]], optional
            Overrides the resolution set in the coil object, by default None

        Raises
        ------
        ValueError
            If the limits are not set in the coil object or as a parameter
        ValueError
            If the resolution is not set in the coil object or as a parameter
        """
        limits = limits or self.limits
        if limits is None:
            raise ValueError("Limits needs to be set")
        resolution = resolution or self.resolution
        if resolution is None:
            raise ValueError("resolution needs to be set")

        dims = [
            int((max_ - min_) // res) for [min_, max_], res in zip(limits, resolution)
        ]

        dx = np.spacing(1e4)
        x = np.linspace(limits[0][0], limits[0][1] - resolution[0] + dx, dims[0])
        y = np.linspace(limits[1][0], limits[1][1] - resolution[0] + dx, dims[1])
        z = np.linspace(limits[2][0], limits[2][1] - resolution[0] + dx, dims[2])
        points = np.array(np.meshgrid(x, y, z, indexing="ij"))
        points = points.reshape((3, -1)).T

        data = self.get_a_field(points, np.eye(4)).reshape((len(x), len(y), len(z), 3))

        affine = np.array(
            [
                [resolution[0], 0, 0, limits[0][0]],
                [0, resolution[1], 0, limits[1][0]],
                [0, 0, resolution[2], limits[2][0]],
                [0, 0, 0, 1],
            ]
        )

        nib.save(nib.Nifti1Image(data, affine), fn)

    def optimize_deformations(
        self, optimization_surface: Msh, affine: npt.NDArray[np.float_]
    ) -> tuple[float, float]:
        """Optimizes the deformations of the coil elements to minimize the distance between the optimization_surface
        and the min distance points (if not present, the coil casing points) while preventing intersections of the
        optimization_surface and the intersect points (if not present, the coil casing points)

        Parameters
        ----------
        optimization_surface : Msh
            The surface the deformations have to be optimized for
        affine : npt.NDArray[np.float_]
            The affine transformation that is applied to the coil

        Returns
        -------
        tuple[float, float]
            The initial mean distance to the surface and the mean distance after optimization

        Raises
        ------
        ValueError
            If the coil has no deformations to optimize
        ValueError
            If the coil has no coil casing and no min distance points and no intersection points
        ValueError
            If an initial intersection between the intersect points (if not present, the coil casing points) and the optimization_surface is detected
        """
        coil_deformations = self.deformations
        if len(coil_deformations) == 0:
            raise ValueError(
                "The coil has no deformations to optimize the coil element positions with."
            )

        if not np.any([np.any(arr) for arr in self.get_casing_coordinates()]):
            raise ValueError(
                "The coil has no coil casing or min_distance/intersection points."
            )

        cost_surface_tree = optimization_surface.get_AABBTree()
        deformation_ranges = np.array([deform.range for deform in coil_deformations])

        intersecting, min_found_distance = self._get_current_deformation_scores(
            cost_surface_tree, affine
        )

        if intersecting:
            raise ValueError("Initial intersection detected.")

        initial_abs_mean_dist = np.abs(
            self._get_current_deformation_scores(cost_surface_tree, affine)[1]
        )
        initial_deformation_settings = np.array(
            [coil_deformation.current for coil_deformation in coil_deformations]
        )
        best_deformation_settings = np.copy(initial_deformation_settings)

        def cost_f_x0(x, x0):
            for coil_deformation, deformation_setting in zip(coil_deformations, x0 + x):
                coil_deformation.current = deformation_setting
            intersecting, distance = self._get_current_deformation_scores(
                cost_surface_tree, affine
            )
            f = initial_abs_mean_dist * intersecting + distance
            if not intersecting:
                nonlocal min_found_distance
                if f < min_found_distance:
                    nonlocal best_deformation_settings
                    best_deformation_settings = x0 + x
                    min_found_distance = f
            return f

        cost_f = lambda x: cost_f_x0(x, initial_deformation_settings)
        min_found_distance = cost_f(np.zeros_like(initial_deformation_settings))

        opt.direct(
            cost_f,
            bounds=list(
                deformation_ranges - initial_deformation_settings[:, np.newaxis]
            ),
        )

        cost_f = lambda x: cost_f_x0(x, 0)
        opt.minimize(
            cost_f,
            x0=np.copy(best_deformation_settings),
            method="L-BFGS-B",
            options={"eps": 0.001, "maxls": 100},
            bounds=deformation_ranges,
        )

        intermediate_best_deformation_settings = np.copy(best_deformation_settings)

        # refine univariate
        for i in range(len(intermediate_best_deformation_settings)):
            cost1 = lambda xx: cost_f(
                np.concatenate(
                    (
                        intermediate_best_deformation_settings[:i],
                        [xx],
                        intermediate_best_deformation_settings[i + 1 :],
                    ),
                    axis=None,
                )
            )
            opt.minimize(
                cost1,
                x0=intermediate_best_deformation_settings[i],
                method="L-BFGS-B",
                options={"eps": 0.001, "maxls": 100},
                bounds=[deformation_ranges[i]],
            )

        for coil_deformation, deformation_setting in zip(
            coil_deformations, best_deformation_settings
        ):
            coil_deformation.current = deformation_setting
        return initial_abs_mean_dist, min_found_distance

    def _get_current_deformation_scores(
        self, cost_surface_tree, affine: npt.NDArray[np.float_]
    ) -> tuple[bool, float]:
        """Evaluates whether or not the intersection points (if not present, the coil casing points) intersect with the cost_surface_tree
        and calculates the mean of the sqrt(distance) between cost_surface_tree and the min distance points (if not present, the coil casing points)

        Parameters
        ----------
        cost_surface_tree : AABBTree
            The AABBTree of the surface to evaluate the current cost for
        affine : npt.NDArray[np.float_]
            The affine transformation that is applied to the coil

        Returns
        -------
        tuple[bool, float]
            Whether or not the intersection points (if not present, the coil casing points) intersect with the cost_surface_tree
            and the mean of the sqrt(distance) between cost_surface_tree and the min distance points (if not present, the coil casing points)
        """
        (
            casing_points,
            min_distance_points,
            intersect_points,
        ) = self.get_casing_coordinates(affine)

        min_distance_points = (
            min_distance_points if len(min_distance_points) > 0 else casing_points
        )
        intersect_points = (
            intersect_points if len(intersect_points) > 0 else casing_points
        )

        return cost_surface_tree.any_point_inside(intersect_points), np.mean(
            np.sqrt(cost_surface_tree.min_sqdist(min_distance_points))
        )
