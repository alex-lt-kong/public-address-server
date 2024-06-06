import argparse
import datetime as dt
import global_vars as gv
import http_service
import logging
import json
import os
import pandas as pd
import random
import threading
import time
import typing
import utils


sound_repository_path = ''
sounds_df = pd.DataFrame()


def main_loop() -> None:

    random.seed()

    while gv.stop_signal is False:
        if gv.reload_schedule:
            df = pd.read_csv(gv.schedule_path)
            gv.reload_schedule = False
            logging.info('Schedule reloaded in main_loop()')

        matched = False
        logging.debug('Loop started')
        for index, row in df.iterrows():
            if (row['时'] != dt.datetime.now().hour or
                    row['分'] != dt.datetime.now().minute):
                logging.debug('Time does not match, waiting for next loop.')
                continue

            matched = True
            logging.info(f'Record {row.to_json(force_ascii=False)} matches, schedule triggered')

            if row['类型'] == '放歌':
                sound_name = sounds_df.sample(n=1).iloc[0]['sounds']
                logging.info(f'[{sound_name}] is selected')

                utils.update_playback_history(sound_name, row['备注'])

            triggers: typing.List[threading.Thread] = []
            client_resps: typing.List[gv.ClientResponse] = []
            devices: typing.List[str] = []
            for i in range(len(gv.client_names_list)):
                if str(row[gv.client_names_list[i]]) != '1':
                    continue
                devices.append(gv.client_names_list[i])
                client_resps.append(gv.ClientResponse())
                if row['类型'] == '报时':
                    device_url = (f'{gv.client_urls_list[i]}?sound_name=0-cuckoo-clock-sound.mp3')
                elif row['类型'] == '放歌':
                    device_url = f'{gv.client_urls_list[i]}?sound_name={sound_name}'
                else:
                    logging.error(f'Encountered unexpected type {row["类型"]}')
                    continue
                triggers.append(threading.Thread(
                    target=utils.call_remote_client, args=(device_url, client_resps[-1])
                ))

            utils.trigger_handler(triggers, devices, client_resps)

        if matched is True:
            for i in range(60):
                if gv.stop_signal is False:
                    time.sleep(1)
        else:
            for i in range(30 if debug_mode else 50):
                if gv.stop_signal is False:
                    time.sleep(1)

    return


def main() -> None:

    ap = argparse.ArgumentParser()
    ap.add_argument('--debug', dest='debug', action='store_true')
    args = vars(ap.parse_args())
    global debug_mode
    debug_mode = args['debug']

    global sounds_df
    with open(os.path.join(gv.config_dir, 'settings.json'), 'r') as json_file:
        json_str = json_file.read()
        gv.settings = json.loads(json_str)

    gv.app_address = gv.settings['app']['address']
    gv.client_urls_list = gv.settings['devices']['urls']
    gv.client_names_list = gv.settings['devices']['names']
    gv.client_sync_delay_list = gv.settings['devices']['sync_delay_ms']
    assert len(gv.client_urls_list) == len(gv.client_names_list)
    assert len(gv.client_names_list) == len(gv.client_sync_delay_list)
    log_path = gv.settings['app']['log_path']
    gv.sound_repository_path = gv.settings['app']['sound_repository_path']
    os.environ['REQUESTS_CA_BUNDLE'] = gv.settings['app']['ca_path']

    logging.basicConfig(
        filename=log_path,
        level=logging.DEBUG if debug_mode else logging.INFO,
        format='%(asctime)s.%(msecs)03d | %(levelname)06s | %(funcName)15s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    if debug_mode is True:
        print('Running in debug mode')
        print(gv.settings)
        logging.debug('Running in debug mode')

    else:
        logging.info('Running in production mode')

    sounds_df = pd.read_csv(os.path.join(gv.config_dir, 'sounds.csv'))

    logging.info(f'{gv.app_name} started')

    main_loop_thread = threading.Thread(target=main_loop, args=())
    main_loop_thread.start()

    http_service.start_http_service()
    gv.stop_signal = True
    logging.info(f'{gv.app_name} exited')


if __name__ == '__main__':

    main()
