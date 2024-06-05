from flask import Response

import datetime as dt
import flask
import global_vars as gv
import logging
import os
import requests
import threading
import typing


def trigger_handler(
    triggers: typing.List[threading.Thread],
    device_names: typing.List[str],
    results: typing.List[typing.List[object]]
) -> flask.Response:

    for i in range(len(triggers)):
        triggers[i].start()
        logging.info(f'[trigger {device_names[i]}] started')

    for i in range(len(triggers)):
        triggers[i].join()

    response = ''
    for i in range(len(triggers)):
        if results[i][1] != 200:
            logging.error(
                f'[non-200 response from agent [{device_names[i]}], '
                f'status_code:{results[i][1]}, response_text: {results[i][0]}'
            )
            response += (
                f'设备[{device_names[i]}]: 失败，HTTP代码：{results[i][1]}'
                f'，错误描述：{results[i][0]}\n')
        else:
            response += f'设备[{device_names[i]}]: 成功加入播放列表\n'
            logging.info(
                f'response from device {device_names[i]}: status_code=={results[i][1]}, '
                f'response_text=={results[i][0]}, response_time=={results[i][3]}ms'
            )
    if response == '':
        response = '没有可用的播放设备'

    logging.info(f'[response to client] response_text: {response}')
    return Response(response.replace('\n', '<br>'), 200)


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


def call_remote_client(url: str, results: typing.List[typing.List[object]],
                       index: int) -> None:

    logging.debug(f'url to request: {url}')

    try:
        start = dt.datetime.now()
        r = requests.get(url, auth=(gv.settings['devices']['username'],
                                    gv.settings['devices']['password']), timeout=5)
        response_timestamp = dt.datetime.now()
        response_time = int((response_timestamp - start).total_seconds() * 1000)
        response_text = r.content.decode("utf-8")
        results[index] = [response_text, r.status_code,
                          response_timestamp, response_time]
    except Exception as ex:
        results[index] = [str(ex), -1, dt.datetime.now(), 0]
