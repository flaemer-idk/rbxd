# Standard library imports
import argparse
import sys

# Local application imports
import launcher.subparsers._logic as sub_logic
from routines import _logic as logic

AUX_MODES = (
    sub_logic.launch_mode.PLAYER,
    sub_logic.launch_mode.SERVER,
    sub_logic.launch_mode.STUDIO,
)

@sub_logic.add_aux_args(*AUX_MODES)
def _(
    mode: sub_logic.launch_mode,
    parser: argparse.ArgumentParser,
    sub_parser: argparse.ArgumentParser,
) -> None:
    sub_parser.add_argument(
        '--backend',
        choices=['wine', 'proton'],
        default=None,
        help='Backend to use for running executables (wine or proton).',
    )
    sub_parser.add_argument(
        '--proton-path',
        type=str,
        default=None,
        help='Path to a specific Proton/UMU-Proton build (only used with --backend proton).',
    )

@sub_logic.serialise_aux_args(*AUX_MODES)
def _(
    mode: sub_logic.launch_mode,
    args_ns: argparse.Namespace,
    args_list: list[logic.base_entry],
) -> list[logic.base_entry]:

    if args_ns.backend is None:
        print("Error: No backend chosen. Please select a backend using --backend {wine,proton}.", file=sys.stderr)
        sys.exit(1)

    proton_path = args_ns.proton_path
    if args_ns.backend == 'wine' and proton_path is not None:
        print("Warning: --proton-path is ignored and cleared because the active backend is 'wine'.", file=sys.stderr)
        proton_path = None

    for a in args_list:
        if isinstance(a, logic.popen_entry):
            a.backend = args_ns.backend
            a.proton_path = proton_path

    return []