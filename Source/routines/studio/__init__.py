# Standard library imports
import dataclasses
import functools
import time
import os
import json

# Typing imports
from typing import ClassVar, override

# Local application imports
from routines.rcc import startup_scripts
from config_type.types import wrappers
from .. import _logic as logic
import util.resource
import util.versions
import game_config
import logger


@dataclasses.dataclass(kw_only=True, unsafe_hash=True)
class obj_type(logic.bin_entry, logic.loggable_entry, logic.gameconfig_entry):
    BIN_SUBTYPE = util.resource.bin_subtype.STUDIO
    DIRS_TO_ADD: ClassVar = [
        'logs', 'LocalStorage',
        'InstalledPlugins', 'placeIDEState',
        'ClientSettings',
    ]

    launch_delay: float = 0
    warn_drag: bool = True

    @override
    def get_base_url(self) -> str:
        return f'https://{self.web_host}:{self.web_port}'

    @override
    def get_app_base_url(self) -> str:
        return self.get_base_url()

    @override
    def __post_init__(self) -> None:
        super().__post_init__()

        if self.web_host == 'localhost':
            self.web_host = '127.0.0.1'

    @override
    def retr_version(self) -> util.versions.rōblox:
        return self.game_config.retr_version()

    def save_starter_scripts(self) -> None:
        server_path = self.get_versioned_path(os.path.join(
            'Content',
            'Scripts',
            'CoreScripts',
            'RFDStarterScript.lua',
        ))
        with open(server_path, 'w', encoding='utf-8') as f:
            startup_script = startup_scripts.get_script(self.game_config)
            f.write(startup_script)

    def setup_vulkan_and_fvars(self) -> None:
        studio_flags = {
            "FFlagDebugGraphicsDisableDirect3D11": "False",
            "FFlagDebugGraphicsPreferVulkan": "False",
            "FFlagDebugGraphicsPreferOpenGL": "False",
        }

        client_settings_dir = self.get_versioned_path('ClientSettings')
        os.makedirs(client_settings_dir, exist_ok=True)

        for filename in ('ClientAppSettings.json', 'StudioAppSettings.json'):
            path = os.path.join(client_settings_dir, filename)
            json_data = {}
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        json_data = json.load(f)
                except Exception:
                    json_data = {}

            json_data.update(studio_flags)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent='\t')

    @functools.cache
    def setup_place(self) -> str:
        rbx_uri = self.game_config.server_core.place_file.rbxl_uri
        if rbx_uri.uri_type == wrappers.uri_type.LOCAL:
            assert isinstance(rbx_uri.value, wrappers.path_str)
            place_path = os.path.normpath(str(rbx_uri.value))
        else:
            new_path = util.resource.retr_full_path(
                util.resource.dir_type.MISC,
                "_.rbxl",
            )
            rbxl_data = rbx_uri.extract()
            if rbxl_data is None:
                raise Exception('RBXL was not found.')
            with open(new_path, 'wb') as f:
                f.write(rbxl_data)
            place_path = new_path

        return util.resource.convert_to_winepath(place_path)

    @override
    def bootstrap(self) -> None:
        super().bootstrap()
        self.save_app_settings()
        self.make_aux_directories()
        self.save_starter_scripts()
        self.setup_vulkan_and_fvars()
        time.sleep(self.launch_delay)

        studio_exe = self.get_versioned_path('RobloxStudioBeta.exe')
        studio_dir = os.path.dirname(studio_exe)
        place_arg = self.setup_place()
        
        self.init_popen(
            exe_path=studio_exe,
            cmd_args=(
                '-localPlaceFile',
                place_arg,
            ),
            cwd=studio_dir,
        )

    @override
    def wait(self):
        super().wait()
        self.kill()
