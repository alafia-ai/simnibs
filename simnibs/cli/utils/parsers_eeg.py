from .helpers import CommandLineParser


write_format_eeg = CommandLineParser('format', dict(
    metavar = "format",
    help = "Format in which to write the forward solution.",
    required=True,
    dest = "format",
))
write_format_mne = CommandLineParser('mne', dict(
    help = "Write to MNE-Python format.",
    # description = "stuff",
))
write_format_fieldtrip = CommandLineParser('fieldtrip', dict(
    help = "Write to FieldTrip format.",
    # description = "stuff",
))

read_format_eeg = CommandLineParser('format', dict(
    metavar = "format",
    help = "Format from which to read.",
    required=True,
    dest = "format",
))
read_format_mne = CommandLineParser('mne', dict(
    help = "Read information from MNE-Python files.",
    # description = "stuff",
))
read_format_fieldtrip = CommandLineParser('fieldtrip', dict(
    help = "Read information from FieldTrip files.",
    # description = "stuff",
))