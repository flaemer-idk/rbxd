import threading
import time


class PresenceStore:
    '''
    Tracks who is currently on the server.

    Populated when a player joins (in `perform_and_send_join`), cleared when the RCC
    reports the player left (`POST /rfd/player-left`). In-memory only: a server restart
    empties it, which is fine because RCC re-joins everyone.
    '''

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._players: dict[int, dict] = {}

    def joined(self, id_num: int, user_code: str, username: str) -> None:
        with self._lock:
            self._players[id_num] = {
                'user_code': user_code,
                'username': username,
                'joined_at': time.time(),
            }

    def left(self, id_num: int) -> dict | None:
        with self._lock:
            return self._players.pop(id_num, None)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {'id_num': id_num, **data}
                for id_num, data in self._players.items()
            ]
