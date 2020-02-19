# distutils: language = c++
# cython: language_level=3

from libcpp cimport bool
from libcpp.vector cimport vector
from libcpp.pair cimport pair
from libc.math cimport abs
import cython
import numpy as np
cimport numpy as np


cdef extern from "_mesh.cpp" nogil:
    int _mesh_image(
        char *fn_image, char *fn_out, float facet_angle,
        float facet_size, float facet_distance,
        float cell_radius_edge_ratio, float cell_size,
        bool optimize)
    int _mesh_image_sizing_field(
        char *fn_image, char *fn_out, float facet_angle,
        float facet_size, float facet_distance,
        float cell_radius_edge_ratio, float *sizing_field,
        bool optimize)
    int _mesh_surfaces(
        vector[char *]filenames, vector[pair[int, int]] incident_subdomains,
        char *fn_out,
        float facet_angle, float facet_size, float facet_distance,
        float cell_radius_edge_ratio, float cell_size,
        bool optimize)
    int _check_self_intersections(
        float *vertices, int n_vertices, int *faces, int n_faces)
    pair[vector[int], vector[float]] _segment_triangle_intersection(
        float* vertices, int n_vertices, int* tris, int n_faces,
        float* segment_start, float* segment_end, int n_segments)


def mesh_image(fn_image, fn_out, float facet_angle, float facet_size,
               float facet_distance, float cell_radius_edge_ratio, float cell_size,
               bool optimize):
    ret =  _mesh_image(
        fn_image, fn_out, facet_angle,
        facet_size, facet_distance,
        cell_radius_edge_ratio, cell_size,
        optimize
    )
    return ret


def mesh_image_sizing_field(
    fn_image, fn_out, float facet_angle, float facet_size,
   float facet_distance, float cell_radius_edge_ratio, sizing_field,
   bool optimize):

    cdef np.ndarray[float, ndim=3] sizing_field_ = np.array(
        sizing_field, dtype=np.float32, order='F'
    )

    ret =  _mesh_image_sizing_field(
        fn_image, fn_out, facet_angle,
        facet_size, facet_distance,
        cell_radius_edge_ratio, &sizing_field_[0, 0, 0],
        optimize
    )
    return ret


def mesh_surfaces(fn_surfaces, incident_subdomains, fn_out,
                  float facet_angle, float facet_size,
                  float facet_distance, float cell_radius_edge_ratio, float cell_size,
                  bool optimize):
    cdef vector[pair[int, int]] subdomains_pairs = incident_subdomains
    cdef vector[char *] surfaces = fn_surfaces
    ret  = _mesh_surfaces(
      surfaces, subdomains_pairs, fn_out,
      facet_angle, facet_size, facet_distance,
      cell_radius_edge_ratio, cell_size,
      optimize
    )
    return ret


def segment_triangle_intersection(vertices, faces, segment_start, segment_end):
    ''' Calculates the intersection between a triangular mesh and line segments
    '''
    cdef np.ndarray[float] vert = np.ascontiguousarray(vertices, dtype=np.float32).reshape(-1)
    cdef np.ndarray[int] fac = np.ascontiguousarray(faces, dtype=np.int32).reshape(-1)
    cdef np.ndarray[float] ss = np.ascontiguousarray(segment_start, dtype=np.float32).reshape(-1)
    cdef np.ndarray[float] se = np.ascontiguousarray(segment_end, dtype=np.float32).reshape(-1)
    cdef pair[vector[int], vector[float]] out

    out = _segment_triangle_intersection(
        &vert[0], len(vertices), &fac[0], len(faces),
        &ss[0], &se[0], len(segment_start)
    )

    pairs = np.array(out.first, dtype=int).reshape(-1, 2)
    positions = np.array(out.second, dtype=float).reshape(-1, 3)

    return pairs, positions
