from web_server._logic import web_server_handler, server_path


@server_path('/rfd/player-left')
def _(self: web_server_handler) -> bool:
    # RCC informs us that a player has left the game.
    # Only trust requests which come from the RCC itself.
    assert self.is_privileged

    id_num = int(self.query['userId'])
    entry = self.server.presence.left(id_num)

    print(
        f'[presence] player left: userId={id_num}, '
        f'user_code={entry["user_code"] if entry else "?"}'
    )

    self.send_data(b'OK')
    return True


@server_path('/rfd/presence')
def _(self: web_server_handler) -> bool:
    # Who is currently on this server.
    self.send_json(self.server.presence.snapshot())
    return True
