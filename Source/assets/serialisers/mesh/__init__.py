from . import rbxmesh


def get_version(original_data: bytes) -> str:
    return rbxmesh.get_mesh_version(original_data)


def check(original_data: bytes) -> bool:
    try:
        get_version(original_data)
        return True
    except Exception:
        return False


def parse(original_data: bytes) -> bytes | None:
    try:
        mesh_version = rbxmesh.get_mesh_version(original_data)
    except Exception:
        return None

    if mesh_version < "4.01":
        return original_data

    try:
        mesh_data = rbxmesh.read_mesh_data(original_data)
        if len(mesh_data.bones) > 0:
            return bytes(rbxmesh.export_mesh_v4(mesh_data))
        else:
            return bytes(rbxmesh.export_mesh_v2(mesh_data))
    except Exception as e:
        print(f'Warning: mesh conversion failed ({e}); serving original data.')
        return original_data
