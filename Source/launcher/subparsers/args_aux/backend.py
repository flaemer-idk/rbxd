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
    default_backend = 'windows' if sys.platform == 'win32' else None
    sub_parser.add_argument(
        '--backend',
        choices=['wine', 'proton', 'windows'],
        default=default_backend,
        help='Backend to use for running executables (wine, proton, or windows).',
    )
    sub_parser.add_argument(
        '--proton-path',
        type=str,
        default=None,
        help='Path to a specific Proton build (only used with --backend proton).',
    )
    sub_parser.add_argument(
        '--wine-path',
        type=str,
        default=None,
        help='Path to a specific Wine executable (only used with --backend wine).',
    )
    sub_parser.add_argument(
        '--wine-prefix',
        type=str,
        default=None,
        help='Path to the Wine prefix directory (only used with --backend wine).',
    )

@sub_logic.serialise_aux_args(*AUX_MODES)
def _(
    mode: sub_logic.launch_mode,
    args_ns: argparse.Namespace,
    args_list: list[logic.base_entry],
) -> list[logic.base_entry]:

    if args_ns.backend is None:
        print("Error: No backend chosen. Please select a backend using --backend {wine,proton,windows}.", file=sys.stderr)
        sys.exit(1)

    proton_path = args_ns.proton_path
    wine_path = args_ns.wine_path
    wine_prefix = args_ns.wine_prefix

    if args_ns.backend != 'proton':
        proton_path = None
    if args_ns.backend != 'wine':
        wine_path = None
        wine_prefix = None

    for a in args_list:
        if isinstance(a, logic.popen_entry):
            a.backend = args_ns.backend
            a.proton_path = proton_path
            a.wine_path = wine_path
            a.wine_prefix = wine_prefix

    return []