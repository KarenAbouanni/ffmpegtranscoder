# Copyright (C) 2019 Thomas Hess <thomas.hess@udo.edu>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from argparse import ArgumentParser
import itertools
from pathlib import Path
from typing import NamedTuple, Optional, List, Iterable, Union

import av1transcoder.constants

__all__ = [
    "CropValues",
    "Namespace",
    "parse_args",
]


# These classes derived from built-in types directly encode semantic restrictions in the type system.

class NonNegativeInt(int):
    """Used for pixel crop values. Only positive integers and zero are allowed."""
    def __new__(cls, *args, **kwargs):
        new: NonNegativeInt = super(NonNegativeInt, cls).__new__(cls, *args, **kwargs)
        if new < 0:
            raise ValueError(f"Invalid number. Expected a non-negative integer. Got {new}.")
        return new


class PositiveInt(int):
    """Used for scene cut lengths and limiting various values. Negative numbers and zero are forbidden."""
    def __new__(cls, *args, **kwargs):
        new: PositiveInt = super(PositiveInt, cls).__new__(cls, *args, **kwargs)
        if new <= 0:
            raise ValueError(f"Invalid number. Expected a positive integer. Got {new}.")
        return new


class NormalizedFloat(float):
    """Used for the scene cut threshold. Implements a float in the interval (0, 1]."""
    def __new__(cls, *args, **kwargs):
        new: NormalizedFloat = super(NormalizedFloat, cls).__new__(cls, *args, **kwargs)
        if not (0 < new <= 1):
            raise ValueError(f"Invalid number. Expected a floating point value in the interval (0, 1]. Got {new}.")
        return new


class CropValues(NamedTuple):

    top: NonNegativeInt
    bottom: NonNegativeInt
    left: NonNegativeInt
    right: NonNegativeInt

    @property
    def crop_height(self):
        return self.top + self.bottom

    @property
    def crop_width(self):
        return self.left + self.right


class Namespace(NamedTuple):
    """
    Mocks the namespace generated by the ArgumentParser. Used for type checking and hinting.
    This has to be manually kept in sync with the actual implementation below for accurate 
    static analysis.
    """
    input_files: List[Path]
    output_dir: Optional[Path]
    temp_dir: Optional[Path]
    keep_temp: bool
    force_overwrite: bool

    scene_cut_threshold: NormalizedFloat
    min_scene_length: PositiveInt
    max_scene_length: PositiveInt
    enable_single_pass_encode: bool
    encoder_parameters: str
    global_parameters: str
    max_concurrent_encodes: PositiveInt
    deinterlace: bool
    dump_commands: str
    limit_encodes: PositiveInt
    crop_values: Union[Iterable[CropValues], Iterable[None]]  # Either CropValues for each input or for none at all

    verbose: bool
    cutelog_integration: bool
    ffmpeg: str
    ffprobe: str
    ffmpeg_base: Optional[str]


def _generate_argument_parser() -> ArgumentParser:
    """
    Generates the argument parser.
    
    BEWARE: When using this directly, make sure to implement all option dependencies in the Namespace returned
    from parsing the arguments with the returned argument parser. Like --dump-commands implying --keep-temp.
    Recommended: Use the module-global function parse_args() instead of this function.
    :return: ArgumentParser instance
    """
    
    # BEWARE: If changing this function, always update the Namespace NamedTuple above,
    # if the structure of the parsing result will change.
    description = "Transcode video files to AV1. This program takes input video files and transcodes the video track " \
                  "to the AV1 format using the libaom-av1 reference encoder."
    epilog = "The resulting files are named like <input_file_name>.AV1.mkv and are placed alongside the input file, " \
             "or into the output directory given by --output-dir. During the encoding process, each input file will " \
             "have it’s own temporary directory named <input_file_name_with_extension>.temp. " \
             "The temporary directory is placed according to the placement rules, preferring --temp-dir over " \
             "--output-dir over the input file’s directory. " \
             "The output files will only contain video tracks. You have to add back other tracks yourself, " \
             "like audio or subtitles, and mux them into the container of your choice. " \
             "Files with multiple video tracks are untested and probably won’t work. File names that contain esoteric " \
             "characters like newlines will probably break the ffmpeg concat demuxer and will likely cause failures. " \
             "\nLong arguments can be abbreviated, as long as the abbreviation is unambiguous. Don’t use this feature " \
             "in scripts, because new argument switches might break previously valid abbreviations. Arguments can " \
             "be loaded from files using the @-Notation. Use \"@/path/to/file\" to load arguments from the specified " \
             "file. The file must contain one argument per line. It may be useful to load a set of common arguments" \
             " from a file instead of typing them out on the command line, " \
             "when you can re-use the same set of arguments multiple times."
    parser = ArgumentParser(description=description, fromfile_prefix_chars="@", epilog=epilog)
    parser.add_argument(
        "input_files", action="store", type=Path, metavar="input_file", nargs="+",
        help="Input video files. All given video files will be transcoded to AV1."
    )
    parser.add_argument(
        "-o", "--output-dir", action="store", type=Path,
        help="Store the result in this directory. If set and --temp-dir is unset, also store the temporary data here. "
             "If unset, results are stored alongside the input file."
    )
    parser.add_argument(
        "-t", "--temp-dir", action="store", type=Path,
        help="Store temporary data in this directory. If unset, use the output directory set by --output-dir. If "
             "that is unset, store the temporary data alongside the input data."
    )
    parser.add_argument(
        "-k", "--keep-temp", action="store_true",
        help="Keep temporary data after the transcoding process finished. May help in resolving transcoding issues."
    )
    parser.add_argument(
        "-f", "--force-overwrite", action="store_true",
        help="Force overwriting existing data. If unset and filename collisions are detected, the affected input files "
             "are skipped. If set, existing files will be overwritten."
    )
    parser.add_argument(
        "-s", "--scene-cut-threshold", action="store", type=NormalizedFloat, default=NormalizedFloat(0.3),
        help="Define the threshold value for the scene cut detection filter. "
             "Accepts a decimal number in the range (0,1]. Defaults to %(default)f"
    )
    parser.add_argument(
        "-m", "--min-scene-length", action="store", metavar="SECONDS", type=PositiveInt, default=PositiveInt(30),
        help="Minimal allowed scene duration in seconds. "
             "Adjacent detected scenes are combined to have at least this duration, if possible. "
             "This is not a hard limit. It prevents splitting the input video into many small and "
             "independent encoding tasks to improve encoding efficiency. Defaults to %(default)i"
    )
    # TODO: The logic to convert PTS needs to be implemented
    # parser.add_argument(
    #     "-M", "--max-scene-length", action="store", metavar="SECONDS", type=PositiveInt, default=120,
    #     help="Maximal allowed scene duration in seconds. Longer scenes are split to not exceed this limit. "
    #          "Defaults to %(default)i"
    # )
    parser.add_argument(
        "-1", "--single-pass", action="store_true", dest="enable_single_pass_encode",
        help="Use Single-Pass encoding instead of Two-Pass encoding. Various sources indicate that this is neither "
             "recommended for libaom-av1 nor saves much time compared to Two-Pass encoding."
    )
    # TODO: Maybe add --auto-crop. If done, place both in a mutually exclusive group.
    parser.add_argument(
        "--crop", action="append", nargs=4, dest="crop_values", default=[], metavar=("TOP", "BOTTOM", "LEFT", "RIGHT"),
        type=NonNegativeInt,
        help="Crop the given number of pixels from the input videos. You can specify the option multiple times to give "
             "each input file their own individual crop parameters. If more input files are given than --crop "
             "instances, the last given set of crop values will be used for all remaining input files. BEWARE: "
             "This uses an ffmpeg video filter, thus is incompatible with additional custom video filters given using "
             "--encoder-parameters. Trying to use --crop and a custom video filter at the same time will cause ffmpeg "
             "to fail."
    )
    parser.add_argument(
        "-e", "--encoder-parameters", action="store", metavar="STRING",
        # Maybe:
        # default="-cpu-used 4 -crf 20 -row-mt 1 -frame-parallel 0 -tiles 2x2 -tile-columns 1 -tile-rows 1 -threads 0"
        # As of writing this, tiles can produce corrupted frames, so disable default tile usage for now.
        # To have more consistent CPU utilization, disable ffmpeg-internal threading and rely on our -c parameter only.
        default="-pix_fmt yuv420p10le -cpu-used 4 -crf 15 -frame-parallel 0 -threads 1 -auto-alt-ref 1 "
                "-lag-in-frames 8 -enable-cdef 1 -enable-global-motion 1 -enable-intrabc 1",
        help="Add custom encoder parameters to the encoding process. Add all parameters as a single, quoted string. "
             "These parameters will be passed directly to all ffmpeg processes doing the encoding work. As an example, "
             "the default value is '%(default)s', which is tuned for high quality encodes of SD material, "
             "for example from DVD sources. BEWARE: Due to a bug in Python argument parser "
             "(https://bugs.python.org/issue9334), the parameters MUST NOT begin with a dash (-) when used as "
             "--encoder-parameters \"<parameters>\". You MUST begin the quoted custom parameter string with a "
             "space character or use = to specify the string, like --encoder-parameters=\"-your-parameters-here\". "
    )
    parser.add_argument(
        "-g", "--global-parameters", action="store", metavar="STRING", default="",
        help="Add custom global parameters to all ffmpeg processes. These are passed in as the first arguments to "
             "ffmpeg before the input file and can be used to enable hardware acceleration or similar global switches. "
             "Example: '-hwaccel cuvid'. When using this to enable hardware decoding, ensure that the HW decoder "
             "can handle at least --max-concurrent-encodes parallel decoder instances. "
             "Default is to not add parameters at all, leaving everything at the default settings. BEWARE: The issue "
             "described for --encoder-parameters applies here, too."
    )
    parser.add_argument(
        "-c", "--max-concurrent-encodes", action="store", type=PositiveInt, default=8,
        help="Run up to this many ffmpeg instances in parallel. Takes a positive integer, defaults to %(default)i"
    )
    parser.add_argument(
        "--dump-commands", action="store", choices=["yes", "no", "only"], default="no",
        help="Dump executed ffmpeg commands in text files for later examination or manual execution. The files will "
             "be placed in the temporary directory. If set to 'only', "
             "this program will only dump the command lines but not actually execute encoding tasks. The scene "
             "detection will always be executed even if set to 'only', "
             "because the later steps require the data to be present. "
             "Defaults to '%(default)s'. Setting to a non-default value implies setting '--keep-temp'."
    )
    parser.add_argument(
        "--deinterlace", action="store_true",
        help="Deinterlace the interlaced input video using the yadif video filter. BEWARE: "
             "This uses an ffmpeg video filter, thus is incompatible with additional custom video filters given using "
             "--encoder-parameters. If you use custom video filters or require another deinterlacer, like IVTC, "
             "add the de-interlace filter to your filter chain instead of using this option."
    )
    parser.add_argument(
        "-L", "--limit-encodes", action="store", metavar="NUMBER", type=PositiveInt,
        help="Stop after encoding this number of scenes. Useful, if you plan to split the encoding process "
             "over multiple sessions. If given, this program will encode this %(metavar)s of "
             "previously not encoded scenes. Only if all scenes are finished, the final result will be assembled from "
             "scenes. Default is to not limit the number of encodes. "
             "For the sake of this option, the two encodes needed for a Two-Pass encode count as one encode towards "
             "this limit. For now, setting this option implies --keep-temp."
    )
    parser.add_argument(
        "-v", "--version", action="version",
        version=f"{av1transcoder.constants.PROGRAMNAME} Version {av1transcoder.constants.VERSION}"
    )
    parser.add_argument(
        "-V", "--verbose",
        action="store_true",
        help="Increase output verbosity. Also show debug messages on the standard output."
    )
    parser.add_argument(
        "--cutelog-integration",
        action="store_true",
        help="Connect to a running cutelog instance with default settings to display the full program log. "
             "See https://github.com/busimus/cutelog for details."
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg", metavar="EXECUTABLE_NAME",
        help="Specify the ffmpeg executable name. "
             "Can be a relative or absolute path or a simple name (i.e. an executable name without path separators). "
             "If given a simple name, the system PATH variable will be searched. Defaults to \"%(default)s\""
    )
    parser.add_argument(
        "--ffprobe",
        default="ffprobe", metavar="EXECUTABLE_NAME",
        help="Specify the ffprobe executable name. "
             "Can be a relative or absolute path or a simple name (i.e. an executable name without path separators). "
             "If given a simple name, the system PATH variable will be searched. Defaults to \"%(default)s\""
    )
    parser.add_argument(
        "--ffmpeg-base",
        default=None,
        metavar="DIRECTORY",
        help="Specify the path to a custom ffmpeg installation, for example \"/opt/ffmpeg/bin\". "
             "If given, both --ffmpeg and --ffprobe arguments are treated as a path relative to this path. "
             "Not set by default."
    )
    return parser


def parse_args() -> Namespace:
    """
    Generates the argument parser and use it to parse the command line arguments.
    Implement all argument dependencies, as given in the help descriptions.
    :return: Parsed command line arguments
    """
    args: Namespace = _generate_argument_parser().parse_args()
    if args.dump_commands != "no" or args.limit_encodes is not None:
        args.keep_temp = True
    # Takes the plain 4-tuple NonNegativeInt as parsed by argparse and packs them into CropValues instances.
    # This fulfils the type promise made by the Namespace class. action="append" guarantees that args.crop contains
    # at least an empty list, therefore this list comprehension always works.
    crop_values = [CropValues(*values) for values in args.crop_values]
    if crop_values:
        # The last crop value is repeated and re-used for all input files.
        # This is an infinite iterator, which is meant to be used inside a zip()
        args.crop_values = itertools.chain(
            crop_values, itertools.repeat(crop_values[-1])
        )
    else:
        args.crop_values = itertools.repeat(None, times=len(args.input_files))
    return args