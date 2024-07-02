from typing import List

import datetime as dt
import global_vars as gv
import logging
import os
import requests
import threading
import time


def trigger_handler(
    device_names: List[str], sound_name: str
) -> List[gv.ClientResponse]:

    # Call the health check endpoint to test latency before making the real API call
    hc_resps = health_check_handler(device_names)
    max_latency = 0
    for i in range(len(hc_resps)):
        logging.info(
            f'Latency of {device_names[i]}: {hc_resps[i].response_latency_ms}ms'
        )
        if max_latency < hc_resps[i].response_latency_ms:
            max_latency = hc_resps[i].response_latency_ms

    th_triggers: List[threading.Thread] = [None] * len(device_names)
    client_resps = [None] * len(device_names)
    for i in range(len(device_names)):
        url = (f'{gv.devices[device_names[i]]["urls"]}?sound_name={sound_name}&'
               f'delay_ms={max_latency - hc_resps[i].response_latency_ms}')
        client_resps[i] = gv.ClientResponse()
        th_triggers[i] = threading.Thread(
            target=call_remote_client,
            args=(device_names[i], url, client_resps[i])
        )
        th_triggers[i].start()
        logging.info(f'trigger thread for {device_names[i]} started, URL: {url}')

    for t in th_triggers:
        t.join()

    for i in range(len(client_resps)):
        if client_resps[i].status_code >= 200 and client_resps[i].status_code < 300:
            logging.info(f'Response from device {device_names[i]}: {client_resps[i]}')
        else:
            logging.error(f'Response from device {device_names[i]}: {client_resps[i]}')

    return client_resps


def health_check_handler(device_names: List[str]) -> List[gv.ClientResponse]:
    triggers: List[threading.Thread] = [None] * len(device_names)
    client_resps: List[gv.ClientResponse] = [None] * len(device_names)
    for i in range(len(device_names)):
        url = (f'{gv.devices[device_names[i]]["urls"]}health_check/')
        client_resps[i] = gv.ClientResponse()
        triggers[i] = threading.Thread(
            target=call_remote_client,
            args=(device_names[i], url, client_resps[i])
        )
        triggers[i].start()
        logging.info(f'health check thread for {device_names[i]} started')

    for t in triggers:
        t.join()

    for i in range(len(client_resps)):
        if client_resps[i].status_code >= 200 and client_resps[i].status_code < 300:
            logging.info(f'Response from device {device_names[i]}: {client_resps[i]}')
        else:
            logging.error(f'Response from device {device_names[i]}: {client_resps[i]}')

    return client_resps


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


def call_remote_client(device_name: str, url: str, client_resp: gv.ClientResponse) -> None:

    logging.debug(f'url to request: {url}')

    try:
        start = time.time()
        r = requests.get(url, auth=(gv.devices[device_name]['username'],
                                    gv.devices[device_name]['password']),
                         timeout=5)
        client_resp.response_text = r.content.decode("utf-8")
        client_resp.status_code = r.status_code
    except Exception as ex:
        client_resp.response_text = str(ex)
        client_resp.status_code = 500
    client_resp.response_latency_ms = int((time.time() - start) * 1000)
