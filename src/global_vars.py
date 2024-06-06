from dataclasses import dataclass

from typing import Dict, List
import os
import typing

app_name = 'public-address-server'
app_address = ''
# app_dir: the app's real address on the filesystem
app_dir = os.path.dirname(os.path.realpath(__file__))
config_dir = os.path.join(app_dir, '..', 'configs')
settings: Dict[str, typing.Any]
users_path = os.path.join(config_dir, 'users.json')
playback_history_file_path = os.path.join(app_dir, 'playback-history.csv')
schedule_path = os.path.join(config_dir, 'schedule.csv')

stop_signal = False
debug_mode = False
reload_schedule = True

client_urls_list: List[str] = []
client_names_list: List[str] = []
client_sync_delay_list: List[int] = []


@dataclass(init=False)
class ClientResponse:

    response_text: str
    status_code: int
    response_latency_ms: int

    def __str__(self):
        return (f'status_code: {self.status_code}, '
                f'response_text: {self.response_text}, '
                f'response_latency_ms: {self.response_latency_ms}')

    __repr__ = __str__
