# Standard library imports
import argparse
import dataclasses
import itertools

# Local application imports
import game_config as config
import logger.flog_table
import logger.bcolors
import util.const
import logger

from routines import web
from routines import _logic as logic

import launcher.subparsers._logic as sub_logic


@sub_logic.add_args(sub_logic.launch_mode.WEBSERVER)
def subparse(
    parser: argparse.ArgumentParser,
    subparser: argparse.ArgumentParser,
) -> None:

    subparser.add_argument(
        '--config_path',
        '--config',
        '-cp',
        type=str,
        nargs='*',
        default=[],
        help='Game-specific options.  When omitted, a generic CDN config is generated instead (version v347, shared asset pool `data/Assets`).',
    )
    ip_version = subparser.add_mutually_exclusive_group()

    ip_version.add_argument(
        '--ipv4_only',
        '--ipv4-only',
        action='store_true',
        help='Run the webserver using IPv4 only.',
    )
    ip_version.add_argument(
        '--ipv6_only',
        '--ipv6-only',
        action='store_true',
        help='Run the webserver using IPv6 only.',
    )
    subparser.add_argument(
        '--web_port', '--webserver_port', '-wp', '-p',
        type=int,
        nargs='*',
        default=[],
        help='Port number for which to run the web server.',
    )

    log_group = subparser.add_mutually_exclusive_group()
    log_group.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppresses console output.',
    )
    log_group.add_argument(
        '--loud',
        action='store_true',
        help='Makes the webserver console output very verbose.',
    )

    subparser.add_argument(
        '--no_colour', '--no_color',
        action='store_true',
        help='Suppresses ANSI colour codes.',
    )


def gen_log_filter(
    parser: argparse.ArgumentParser,
    args_ns: argparse.Namespace,
) -> logger.obj_type:
    if args_ns.quiet:
        result = logger.PRINT_QUIET
    elif args_ns.loud:
        result = logger.PRINT_LOUD
    else:
        result = logger.PRINT_REASONABLE

    if args_ns.no_colour:
        result = dataclasses.replace(
            result,
            bcolors=logger.bcolors.BCOLORS_INVISIBLE,
        )

    return result


@sub_logic.serialise_args(sub_logic.launch_mode.WEBSERVER)
def _(
    parser: argparse.ArgumentParser,
    args_ns: argparse.Namespace,
) -> list[logic.base_entry]:
    if len(args_ns.config_path) > 0:
        game_configs = [
            config.get_cached_config(v)
            for v in args_ns.config_path
        ]
    else:
        # Безплейсовый CDN-инстанс: раздаёт общий пул ассетов и скины.
        game_configs = [config.generate_cdn_config()]

    has_ipv6: bool = not args_ns.ipv4_only
    has_ipv4: bool = not args_ns.ipv6_only

    web_routine_args = list[logic.base_entry]()
    log_filter = gen_log_filter(
        parser, args_ns,
    )

    def gen_next_seq_port(ports: list[int | None]):
        last_used = util.const.RFD_DEFAULT_PORT - 1
        for p in ports:
            if p is not None:
                last_used = p
            else:
                last_used += 1
            yield last_used

    web_port_gen = gen_next_seq_port(args_ns.web_port)

    for (
        web_port, game_config,
    ) in itertools.zip_longest(
        web_port_gen, game_configs,
    ):
        if has_ipv6:
            # IPv6 goes first since `localhost` also resolves first to [::1] on the client.
            web_routine_args.append(web.obj_type(
                web_port=web_port,
                is_ssl=True,
                is_ipv6=True,
                server_mode=web.SERVER_MODE_TYPE.RCC,
                logger=log_filter,
                game_config=game_config,
            ))
        if has_ipv4:
            web_routine_args.append(web.obj_type(
                web_port=web_port,
                is_ssl=True,
                is_ipv6=False,
                server_mode=web.SERVER_MODE_TYPE.RCC,
                logger=log_filter,
                game_config=game_config,
            ))

    return web_routine_args
