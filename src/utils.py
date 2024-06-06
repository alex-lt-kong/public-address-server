from typing import List

import datetime as dt
import global_vars as gv
import logging
import os
import requests
import threading
import time


def trigger_handler(
    triggers: List[threading.Thread],
    device_names: List[str],
    client_resps: List[gv.ClientResponse]
) -> str:

    for i in range(len(triggers)):
        triggers[i].start()
        logging.info(f'[trigger {device_names[i]}] started')

    for i in range(len(triggers)):
        triggers[i].join()

    response: str = ''
    for i in range(len(triggers)):
        if client_resps[i].status_code < 200 or client_resps[i].status_code >= 300:
            logging.error(f'response from device [{device_names[i]}], {client_resps[i]}')
            response += (
                f'设备[{device_names[i]}]: 失败，HTTP代码：{client_resps[i].status_code}'
                f'，错误描述：{client_resps[i].response_text}\n')
        else:
            response += f'设备[{device_names[i]}]: 成功加入播放列表\n'
            logging.info(f'response from device {device_names[i]}: {str(client_resps[i])}')
    if response == '':
        response = '没有可用的播放设备'

    logging.info(f'[response to client] response_text:\n{response}')
    return response


def update_playback_history(sound_name: str, reason: str) -> None:

    try:
        max_history_entry = 256
        playback_history = ''
        if os.path.exists(gv.playback_history_file_path):
            with open(gv.playback_history_file_path, 'r') as f:
                for line in (f.readlines()[-(max_history_entry - 1):]):
                    playback_history += line
        # WARNING: When os.path.basename() is used on a POSIX system to get the
        # base name from a Windows styled path (e.g. "C:\\my\\file.txt"),
        # the entire path will be returned.
        with open(gv.playback_history_file_path, "w") as f:
            playback_history += (
                f'{dt.datetime.now().strftime("%Y-%m-%d %H:%M")},{sound_name},{reason}\n')
            f.write(playback_history)
    except Exception as e:
        logging.error(f'Failed to update playback history: {e}')


def call_remote_client(url: str, client_resp: gv.ClientResponse) -> None:

    logging.debug(f'url to request: {url}')

    try:
        start = time.time()
        r = requests.get(url, auth=(gv.settings['devices']['username'],
                                    gv.settings['devices']['password']),
                         timeout=5)
        client_resp.response_text = r.content.decode("utf-8")
        client_resp.status_code = r.status_code
        client_resp.response_latency_ms = int((time.time() - start) * 1000)

    except Exception as ex:
        client_resp.response_text = str(ex)
        client_resp.status_code = 500
        client_resp.response_latency_ms = -1
