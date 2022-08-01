#!/usr/bin/python3

from emailer import emailer
from flask import Flask, render_template, redirect, Response, request, session

import argparse
import datetime as dt
import flask
import glob
import hashlib
import logging
import json
import os
import pandas as pd
import random
import requests
import shutil
import signal
import sys
import threading
import time
import typing
import waitress

app = Flask(__name__)
app.secret_key = b''
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

app_address = ''
app.config['JSON_AS_ASCII'] = False
app_name = 'public-address-server'
# app_dir: the app's real address on the filesystem
app_dir = os.path.dirname(os.path.realpath(__file__))
settings_path = os.path.join(app_dir, 'settings.json')
sound_repository_path = ''
sounds_df = pd.DataFrame()
schedule_path = os.path.join(app_dir, 'schedule.csv')
settings: typing.Dict[str, typing.Any]
users_path = os.path.join(app_dir, 'users.json')
playback_history_file_path = os.path.join(app_dir, 'playback-history.csv')

reload_schedule = True
stop_signal = False
debug_mode = False
client_urls_list: typing.List[str] = []
client_names_list: typing.List[str] = []


def stop_signal_handler(*args) -> None:

    global stop_signal
    stop_signal = True
    logging.info(f'Signal [{args[0]}] received, exiting')
    sys.exit(0)


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
        max_history_entry = 128
        playback_history = ''
        if os.path.exists(playback_history_file_path):
            with open(playback_history_file_path, 'r') as f:
                for line in (f.readlines()[-(max_history_entry - 1):]):
                    playback_history += line
        # WARNING: When os.path.basename() is used on a POSIX system to get the
        # base name from a Windows styled path (e.g. "C:\\my\\file.txt"),
        # the entire path will be returned.
        with open(playback_history_file_path, "w") as f:
            playback_history += (
                f'{dt.datetime.now().strftime("%Y-%m-%d %H:%M")},{sound_name},{reason}\n')
            f.write(playback_history)
    except Exception as e:
        logging.error(f'Failed to update playback history: {e}')


def call_remote_client(url: str, results: typing.List[typing.List[object]], index: int) -> None:

    logging.debug(f'url to request: {url}')

    try:
        start = dt.datetime.now()
        r = requests.get(url, auth=(settings['devices']['username'], settings['devices']['password']), timeout=5)
        response_timestamp = dt.datetime.now()
        response_time = int((response_timestamp - start).total_seconds() * 1000)
        response_text = r.content.decode("utf-8")
        results[index] = [response_text, r.status_code, response_timestamp, response_time]
    except Exception as ex:
        results[index] = [str(ex), -1, dt.datetime.now(), 0]


def validate_uploaded_schedule(path_to_validate: str) -> typing.Tuple[bool, str]:

    try:
        df = pd.read_csv(path_to_validate)
    except Exception as e:
        return False, f'时间表无法解析：{e}'

    if '时' != df.columns[0] or '分' != df.columns[1] or '类型' != df.columns[2] or '备注' != df.columns[3]:
        return False, '列名称错误：前四列应依次为[时，分，类型，备注]'

    for name in client_names_list:
        if name not in df.columns:
            return False, f'时间表没有定义设备[{name}]的计划'
        invalid_rows = df.loc[~df[name].isin([0, 1])]
        if invalid_rows.shape[0] == 0:
            continue
        return (
            False,
            f'设备[{name}]的计划的值只能是[0,1]，这些行违反了这个规则：\n\n{invalid_rows.to_string(header=False, index=False)}'
        )

    if len(df.columns) != len(client_names_list) + 4:
        return False, '时间表存在额外的列'

    invalid_rows = df.loc[~((df['分'] >= 0) & (df['分'] <= 59))]
    if invalid_rows.shape[0] > 0:
        return (
            False,
            f'数据列"分"的合法范围是0-59，这些行违反了这个规则：\n\n{invalid_rows.to_string(header=False, index=False)}'
        )
    invalid_rows = df.loc[~((df['时'] >= 0) & (df['时'] <= 23))]
    if invalid_rows.shape[0] > 0:
        return (
            False,
            f'数据列"时"的合法范围是0-23，这些行违反了这个规则：\n\n{invalid_rows.to_string(header=False, index=False)}'
        )

    invalid_rows = df.loc[~df['类型'].isin(['报时', '放歌'])]
    if invalid_rows.shape[0] > 0:
        return (
            False,
            f'数据列"类型"的值只能是[报时,放歌]，这些行违反了这个规则：\n\n{invalid_rows.to_string(header=False, index=False)}'
        )
    invalid_rows = df.loc[~df['类型'].isin(['报时', '放歌'])]
    if invalid_rows.shape[0] > 0:
        return (
            False,
            f'数据列"类型"的值只能是[报时,放歌]，这些行违反了这个规则：\n\n{invalid_rows.to_string(header=False, index=False)}'
        )

    return True, ''


@app.route('/upload_schedule/', methods=['GET', 'POST'])
def upload_schedule() -> flask.Response:

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        pass
    else:
        return Response('未登录', status=400)

    global reload_schedule
    logging.info('uploading new schedule')

    if 'schedule-file' not in request.files:
        return Response('没有接收到文件', 400)
    schedule_file = request.files['schedule-file']

    if schedule_file.filename == '':
        return Response('没有选中的文件', 400)

    filename = 'schedule.csv.tmp'
    playback_history_dir = os.path.dirname(playback_history_file_path)
    temp_file_path = os.path.join(playback_history_dir, filename)
    if os.path.isfile(temp_file_path):
        os.remove(temp_file_path)
    schedule_file.save(temp_file_path)

    retval, message = validate_uploaded_schedule(temp_file_path)

    if retval is False:
        logging.warn(f'Invalidate schedule uploaded: {message}')
        return Response(message.replace('\n', '<br>'), 400)

    try:
        shutil.copy(temp_file_path, schedule_path)
    except Exception:
        return Response('应用时间表错误：无法覆盖现有时间表文件', 500)

    reload_schedule = True
    logging.info('New schedule uploaded and applied')
    return Response('时间表已应用', 200)


@app.route('/download_schedule/', methods=['GET'])
def download_schedule() -> flask.Response:

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        pass
    else:
        return Response('未登录', status=400)

    logging.info('Downloading schedule')
    return flask.send_file(
        filename_or_fp=schedule_path,
        as_attachment=True,
        attachment_filename=f'schedule-{dt.datetime.now().hour:02}{dt.datetime.now().minute:02}.csv'
    )


def sanitize_filename(filename: str) -> str:
    # This function may be not robust... but should be good enough
    # for this use case...
    # also, the security is enhanced by the use of send_from_directory()
    error_set = ['/', '\\', ':', '*', '?', '"', '|', '<', '>', ' ']
    for c in filename:
        if c in error_set:
            filename = filename.replace(c, '_')
    if len(filename) > 64:
        filename = filename[:31] + '__' + filename[-31:]
    return filename


@app.route('/play/', methods=['GET'])
def play() -> flask.Response:

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        username = session[f'{app_name}']['username']
    else:
        return Response('未登录', status=400)

    if 'sound_name' in request.args:
        try:
            sound_name = request.args.get('sound_name')
        except Exception as e:
            return Response(f'{e}', status=400)
    else:
        return Response('sound_name not specified', status=400)

    if 'devices' not in request.args:
        return Response('没有选中的播放设备', status=400)
    devices = request.args.get('devices').split(',')
    if len(devices) == 0:
        return Response('没有选中的播放设备', status=400)

    for device in devices:
        if device in client_names_list:
            continue
        return Response(f'[{device}]不在可用设备列表{client_names_list}中', status=400)

    logging.info(f'[{sound_name}] manually played by {username}')

    update_playback_history(sound_name, f'{username}播放')

    triggers: typing.List[threading.Thread] = []
    results: typing.List[typing.List[object]] = []

    for i in range(len(devices)):
        index = client_names_list.index(devices[i])
        results.append([])
        device_url = f'{client_urls_list[index]}?sound_name={sound_name}'
        triggers.append(threading.Thread(target=call_remote_client, args=(device_url, results, i)))

    return trigger_handler(triggers=triggers, device_names=devices, results=results)


@app.route('/client-health-check/', methods=['GET'])
def client_health_check() -> typing.Union[typing.Dict[str, str], flask.Response]:

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        # username = session[f'{app_name}']['username']
        pass
    else:
        return Response('未登录', status=400)
    results: typing.Dict[str, typing.Any] = {}

    for i in range(len(client_urls_list)):
        results[client_names_list[i]] = {}
        try:
            r = requests.get(
                f'{client_urls_list[i]}health_check/',
                auth=(settings['devices']['username'], settings['devices']['password']),
                timeout=5
            )
            if r.status_code == 200:
                results[client_names_list[i]]['status'] = '正常'
            else:
                results[client_names_list[i]]['status'] = '错误'
                results[client_names_list[i]]['content'] = r.content.decode("utf-8")
                results[client_names_list[i]]['status_code'] = r.status_code
        except Exception as e:
            results[client_names_list[i]]['status'] = '错误'
            results[client_names_list[i]]['content'] = str(e)
            results[client_names_list[i]]['status_code'] = -1

    return results


@app.route('/', methods=['GET'])
def index() -> flask.Response:

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        username = session[f'{app_name}']['username']
    else:
        return redirect(f'{app_address}/login/')

    playback_items: typing.List[str] = []
    with open(playback_history_file_path, 'r') as f:
        for line in (f.readlines()):
            playback_items.insert(0, line.replace('\n', '').split(','))
    kwargs = {
        'app_address': app_address,
        'playback_items': playback_items,
        'mode': 'dev' if debug_mode else 'prod',
        'agent_names_list': client_names_list,
        'username': username
    }

    return render_template("index.html", **kwargs)


@app.route('/logout/')
def logout() -> flask.Response:

    if f'{app_name}' in session:
        session[f'{app_name}'].pop('username', None)
    return redirect(f'{app_address}/')


@app.before_request
def make_session_permanent() -> None:
    session.permanent = True
    app.permanent_session_lifetime = dt.timedelta(days=90)


@app.route('/login/', methods=['GET', 'POST'])
def login() -> flask.Response:

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        return redirect(f'{app_address}/')

    if request.method != 'POST':
        return render_template('login.html', error_message='')

    try:
        with open(users_path, 'r') as json_file:
            json_str = json_file.read()
            json_data = json.loads(json_str)
    except Exception as e:
        return render_template(
                'login.html',
                error_message=f'错误：{e}')

    if request.form['username'] not in json_data['users']:
        return render_template(
                'login.html',
                error_message=f'错误：用户[{request.form["username"]}]不存在')

    if (hashlib.sha256(request.form['password'].encode('utf-8')).hexdigest()
            != json_data['users'][request.form['username']]):
        return render_template(
                'login.html',
                error_message='错误：密码错误')
    session[f'{app_name}'] = {}
    session[f'{app_name}']['username'] = request.form['username']

    return redirect(f'{app_address}/')


def main_loop() -> None:

    global stop_signal, reload_schedule

    random.seed()

    while stop_signal is False:
        if reload_schedule:
            df = pd.read_csv(schedule_path)
            reload_schedule = False
            logging.info('Schedule reloaded in main_loop()')

        matched = False
        logging.debug('Loop started')
        for index, row in df.iterrows():
            if (row['时'] == dt.datetime.now().hour and row['分'] == dt.datetime.now().minute) is False:
                logging.debug('Time does not match, waiting for next loop.')
                continue

            matched = True
            logging.info(f'\n{row}\nmatched, schedule trigger started')

            if row['类型'] == '放歌':
                sound_name = sounds_df.sample(n=1).iloc[0]['sounds']
                logging.info(f'[{sound_name}] is selected')

                update_playback_history(sound_name, row['备注'])

            triggers: typing.List[threading.Thread] = []
            results: typing.List[typing.List[object]] = []
            devices: typing.List[str] = []
            idx = 0
            for i in range(len(client_names_list)):
                if str(row[client_names_list[i]]) != '1':
                    continue
                devices.append(client_names_list[i])
                if row['类型'] == '报时':
                    device_url = (f'{client_urls_list[i]}?sound_name=0-cuckoo-clock-sound.mp3')
                elif row['类型'] == '放歌':
                    device_url = f'{client_urls_list[i]}?sound_name={sound_name}'
                else:
                    logging.error(f'Encountered unexpected type {row["类型"]}')
                    continue
                triggers.append(threading.Thread(target=call_remote_client, args=(device_url, results, idx)))
                results.append([None, None, None, None])
                idx += 1

            trigger_handler(triggers=triggers, device_names=devices, results=results)

        if matched is True:
            for i in range(60):
                if stop_signal is False:
                    time.sleep(1)
        else:
            for i in range(30 if debug_mode else 50):
                if stop_signal is False:
                    time.sleep(1)

    return


def main() -> None:

    ap = argparse.ArgumentParser()
    ap.add_argument('--debug', dest='debug', action='store_true')
    args = vars(ap.parse_args())
    global debug_mode
    debug_mode = args['debug']

    global settings, sounds_df
    global app_address, client_urls_list, client_names_list, sound_repository_path
    with open(os.path.join(app_dir, 'settings.json'), 'r') as json_file:
        json_str = json_file.read()
        settings = json.loads(json_str)

    app.secret_key = settings['flask']['secret_key']
    app.config['MAX_CONTENT_LENGTH'] = settings['flask']['max_upload_size']
    app_address = settings['app']['address']
    client_urls_list = settings['devices']['urls']
    client_names_list = settings['devices']['names']
    log_path = settings['app']['log_path']
    sound_repository_path = settings['app']['sound_repository_path']
    os.environ['REQUESTS_CA_BUNDLE'] = settings['app']['ca_path']

    logging.basicConfig(
        filename=log_path,
        level=logging.DEBUG if debug_mode else logging.INFO,
        format='%(asctime)s %(levelname)06s - %(funcName)s: %(message)s',
        datefmt='%Y%m%d-%H%M%S',
    )

    if debug_mode is True:
        print('Running in debug mode')
        print(settings)
        logging.debug('Running in debug mode')

    else:
        logging.info('Running in production mode')

    sounds_df = pd.read_csv(os.path.join(app_dir, 'sounds.csv'))

    signal.signal(signal.SIGINT, stop_signal_handler)
    signal.signal(signal.SIGTERM, stop_signal_handler)
    logging.info(f'{app_name} started')

    main_loop_thread = threading.Thread(target=main_loop, args=())
    main_loop_thread.start()
    th_email = threading.Thread(
        target=emailer.send_service_start_notification,
        kwargs={
            'settings_path': settings_path,
            'service_name': f'{app_name}',
            'path_of_logs_to_send': log_path,
            'delay': 0 if debug_mode else 300
        })
    th_email.start()

    waitress.serve(app, host="127.0.0.1", port=settings['flask']['port'])

    logging.info(f'{app_name} exited')


if __name__ == '__main__':

    main()
