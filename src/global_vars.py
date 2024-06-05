import os
import typing

app_name = 'public-address-server'
app_address = ''
# app_dir: the app's real address on the filesystem
app_dir = os.path.dirname(os.path.realpath(__file__))
config_dir = os.path.join(app_dir, '..', 'configs')
settings: typing.Dict[str, typing.Any]
users_path = os.path.join(config_dir, 'users.json')
playback_history_file_path = os.path.join(app_dir, 'playback-history.csv')
schedule_path = os.path.join(config_dir, 'schedule.csv')

stop_signal = False
debug_mode = False
reload_schedule = True

client_urls_list: typing.List[str] = []
client_names_list: typing.List[str] = []
client_sync_delay_list: typing.List[int] = []
