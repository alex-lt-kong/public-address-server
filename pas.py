#!/usr/bin/python3
# -*- coding: utf-8 -*-

from flask import Flask, render_template, redirect, Response, request, session

import argparse
import datetime as dt
import flask
import glob
import hashlib
import importlib
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
import waitress

app = Flask(__name__)
app.secret_key = b''
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

app_address = ''
app_name = 'public-address-server'
# app_dir: the app's real address on the filesystem
app_dir = os.path.dirname(os.path.realpath(__file__))
settings_path = os.path.join(app_dir, 'settings.json')
sound_repository_path = os.path.join(app_dir, 'sound-repository')
schedule_path = os.path.join(app_dir, 'schedule.csv')
settings = None
users_path = os.path.join(app_dir, 'users.json')
playback_history_file_path = os.path.join(app_dir, 'playback-history.csv')
schedule_dtypes_dict = {'时': int, '分': int, '类型': object,
                        '阳台': int, '客厅': int, '卧室': int,
                        '备注': object}

reload_schedule = True
stop_signal = False
debug_mode = False
agents_url_list, agent_names_list = [], []


def stop_signal_handler(*args):

    global stop_signal
    stop_signal = True
    logging.info(f'Signal [{args[0]}] received, exiting')
    sys.exit(0)


# This function should be kept exactly the same among all
# notification-trigger/notification-agent programs or the same sound_index
# could result in different sound.
def get_song_by_id(sound_index: int):

    music_path = os.path.join(sound_repository_path, 'custom-event/') + '*'
    songs_list = sorted(glob.glob(music_path), key=os.path.getsize)
    for song in songs_list:
        logging.debug(song)
    if songs_list is None or sound_index >= len(songs_list):
        return None, songs_list
    else:
        return songs_list[sound_index], songs_list


def trigger_handler(triggers, device_names, default_response, results):

    format_agent_name = []
    assert len(triggers) == len(device_names)
    for i in range(len(triggers)):

        format_agent_name.append('[{}]'.format(device_names[i]).ljust(6))

        if triggers[i] is not None:
            triggers[i].start()
            logging.info(f'[trigger {format_agent_name[i]}] started')
        else:
            logging.info(f'[trigger {format_agent_name[i]}] NOT started')

    for i in range(len(triggers)):
        if triggers[i] is not None:
            triggers[i].join()

    http_status = 200
    response = ''
    for i in range(len(triggers)):
        if results[i] is not None:
            if results[i][1] != 200:
                logging.error(
                   f'[non-200 response from agent [{format_agent_name[i]}]] '
                   f'status_code:{results[i][1]}, '
                   f'response_text: {results[i][0]}')
                response += (
                    f'{device_names[i]}设备返回错误，代码：{results[i][1]}'
                    f'，错误描述：{results[i][0]}\n')
                http_status = 500
            else:
                logging.info(f'[response from device {format_agent_name[i]}] '
                             f'status_code: {results[i][1]}, '
                             f'response_text: {results[i][0]}, '
                             f'response_time: {results[i][3]}ms')
    if response == '':
        response = default_response

    logging.info(f'[response to client] status_code: {http_status}, '
                 f'response_text: {response}')
    return Response(response.replace('\n', '<br>'), http_status)


def update_playback_history(sound_index: int, sound_name: str, reason: str):

    try:
        max_history_entry = 100
        playback_history = ''
        if os.path.exists(playback_history_file_path):
            with open(playback_history_file_path, 'r') as f:
                for line in (f.readlines()[-(max_history_entry - 1):]):
                    playback_history += line
        # WARNING: When os.path.basename() is used on a POSIX system to get the
        # base name from a Windows styled path (e.g. "C:\\my\\file.txt"),
        # the entire path will be returned.
        with open(playback_history_file_path, "w") as f:
            playback_history += '{},{},{},{}\n'.format(
                                dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                sound_index,
                                sound_name,
                                reason)
            f.write(playback_history)
    except Exception as e:
        logging.error(f'Failed to update playback history: {e}')


def trigger_sender(url: str, results, index: int):

    logging.debug(f'url to request: {url}')

    start = dt.datetime.now()
    r = requests.get(url, auth=('trigger', 'dsfs43srgsKs'))
    response_timestamp = dt.datetime.now()

    response_time = int((response_timestamp - start).total_seconds() * 1000)
    response_text = r.content.decode("utf-8")
    results[index] = [response_text,
                      r.status_code,
                      response_timestamp,
                      response_time]


def validate_uploaded_schedule(path_to_validate):

    try:
        df = pd.read_csv(path_to_validate, dtype=schedule_dtypes_dict)
    except Exception as e:
        return False, f'时间表无法解析：{e}'

    if ('时' not in df.columns or '分' not in df.columns or '类型'
            not in df.columns or '备注' not in df.columns):
        return False, '列名称错误'

    for name in agent_names_list:
        if name not in df.columns:
            return False, '设备名称错误'

    for index, row in df.iterrows():
        try:
            hour = row['时']
            minute = row['分']
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                return False, '时间错误'
        except Exception as e:
            return False, f'时间错误：{e}'

        if row['类型'] != '报时' and row['类型'] != '放歌':
            return False, '类型错误'

        for name in agent_names_list:
            if str(row[name]) != '0' and str(row[name]) != '1':
                return False, '设备设置错误'

    return True, ''


@app.route('/upload_schedule/', methods=['GET', 'POST'])
def upload_schedule():

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
        return Response(message, 400)
    else:
        try:
            shutil.copy(temp_file_path, schedule_path)
        except Exception as e:
            return False, f'应用时间表错误：{e}'

        reload_schedule = True
        logging.info('New schedule uploaded and applied')
        return Response('时间表已应用', 200)

    return Response('未知内部错误', 500)


@app.route('/download_schedule/', methods=['GET'])
def download_schedule():

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        pass
    else:
        return Response('未登录', status=400)

    logging.info('Downloading schedule')
    return flask.send_file(filename_or_fp=schedule_path,
                           as_attachment=True,
                           attachment_filename='schedule-{}{}.csv'
                           .format(dt.datetime.now().hour,
                                   dt.datetime.now().minute))


def sanitize_filename(filename):
    # This function may be not robust enough... but should be good enough
    # for this use case...
    # also, the security is enhanced by the use of send_from_directory()
    error_set = ['/', '\\', ':', '*', '?', '"', '|', '<', '>', ' ']
    for c in filename:
        if c in error_set:
            filename = filename.replace(c, '_')
    if len(filename) > 64:
        filename = filename[:31] + '__' + filename[-31:]
    return filename


@app.route('/download_music/', methods=['GET'])
def download_music():

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        pass
    else:
        return Response('未登录', status=401)

    if 'filename' not in request.args:
        return Response('参数filename未指定', status=400)

    filename = sanitize_filename(request.args['filename'])

    if os.path.isfile(os.path.join(sound_repository_path,
                                   'custom-event/', filename)):
        return flask.send_from_directory(
                   directory=os.path.join(sound_repository_path,
                                          'custom-event/'),
                   filename=filename, as_attachment=True,
                   attachment_filename=filename)

    return Response(f'没有与指定的文件名{filename}对应的歌曲', status=400)


@app.route('/play/', methods=['GET'])
def play():

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        username = session[f'{app_name}']['username']
    else:
        return Response('未登录', status=400)

    if 'sound_index' in request.args:
        try:
            sound_index = int(request.args.get('sound_index'))
        except Exception as e:
            return Response(f'{e}', status=400)
    else:
        return Response('sound_index not specified', status=400)

    if 'devices' not in request.args:
        return Response('没有选中的播放设备', status=400)
    devices = request.args.get('devices').split(',')
    if len(devices) == 0:
        return Response('没有选中的播放设备', status=400)

    for device in devices:
        if device not in agent_names_list:
            return Response(f'[{device}]不在可用设备列表{agent_names_list}中',
                            status=400)

    sound_name, songs_list = get_song_by_id(sound_index)
    if sound_name is None:
        return Response('sound_index 超出范围', status=400)
    sound_name = os.path.basename(sound_name)

    logging.info(f'[{sound_name}] '
                 f'(index = {sound_index}) manually played by {username}')

    update_playback_history(sound_index,
                            sound_name[:-4],
                            f'{username}播放')

    triggers, results = [], []

    for i in range(len(devices)):
        index = agent_names_list.index(devices[i])
        results.append(None)
        device_url = (f'{agents_url_list[index]}?notification_type=custom&'
                      f'sound_index={sound_index}')
        triggers.append(threading.Thread(target=trigger_sender,
                                         args=(device_url, results, i)))

    return trigger_handler(
            triggers=triggers,
            device_names=devices,
            default_response=f'PLAY command accepted by {devices}',
            results=results)


@app.route('/stop/', methods=['GET'])
def stop():

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        username = session[f'{app_name}']['username']
    else:
        return Response('未登录', status=400)

    logging.info('STOP commanded from {}'.format(username))

    triggers = [None] * len(agents_url_list)
    results = [None] * len(agents_url_list)
    for i in range(len(agents_url_list)):
        triggers[i] = threading.Thread(
                target=trigger_sender,
                args=(f'{agents_url_list[i]}clear_playlist/', results, i))

    return trigger_handler(
            triggers=triggers,
            device_names=agent_names_list,
            default_response=f'STOP command accepted by {agent_names_list}',
            results=results)


@app.route('/schedule/', methods=['GET'])
def schedule():

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        username = session[f'{app_name}']['username']
    else:
        return redirect(f'{app_address}/login/')

    df = pd.read_csv(schedule_path, dtype=schedule_dtypes_dict)
    schedule_items = []
    for index, row in df.iterrows():
        schedule_items.append([row['时'],   row['分'],
                               row['类型'], row['阳台'],
                               row['客厅'], row['卧室'], row['备注']])
    return render_template("schedule.html",
                           schedule_items=schedule_items,
                           username=username)


@app.route('/', methods=['GET'])
def index():

    if f'{app_name}' in session and 'username' in session[f'{app_name}']:
        username = session[f'{app_name}']['username']
    else:
        return redirect(f'{app_address}/login/')

    playback_items = []
    with open(playback_history_file_path, 'r') as f:
        for line in (f.readlines()):
            playback_items.insert(0, line.replace('\n', '').split(','))

    return render_template("index.html",
                           playback_items=playback_items,
                           agent_names_list=agent_names_list,
                           username=username)


@app.route('/logout/')
def logout():

    if f'{app_name}' in session:
        session[f'{app_name}'].pop('username', None)
    return redirect(f'{app_address}/')


@app.before_request
def make_session_permanent():
    session.permanent = True
    app.permanent_session_lifetime = dt.timedelta(days=90)


@app.route('/login/', methods=['GET', 'POST'])
def login():

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


def main_loop():

    global stop_signal, reload_schedule

    random.seed()

    while stop_signal is False:
        if reload_schedule:
            df = pd.read_csv(schedule_path, dtype=schedule_dtypes_dict)
            reload_schedule = True
            logging.debug('Schedule reloaded')

        matched = False
        logging.debug('Loop started')
        for index, row in df.iterrows():
            if (row['时'] == dt.datetime.now().hour
                    and row['分'] == dt.datetime.now().minute) is False:
                logging.debug('Time does not match, waiting for next loop.')
                continue

            matched = True
            logging.info('\n{}\nmatched, schedule trigger started'.format(row))
            _, songs_list = get_song_by_id(0)
            songs_count = len(songs_list)
            sound_index = random.randint(0, songs_count - 1)

            if row['类型'] == '放歌':
                sound_name = os.path.basename(songs_list[sound_index])
                logging.info(f'[{sound_name}] (index = {sound_index}) '
                             'is selected')

                update_playback_history(sound_index,
                                        sound_name[:-4],
                                        row['备注'])

            triggers = []
            results = [None] * len(agents_url_list)
            devices = []
            for i in range(len(agent_names_list)):
                if str(row[agent_names_list[i]]) != '1':
                    continue
                devices.append(agent_names_list[i])
                if row['类型'] == '报时':
                    device_url = (f'{agents_url_list[i]}?notification_type='
                                  'chiming')
                elif row['类型'] == '放歌':
                    device_url = (f'{agents_url_list[i]}?notification_type'
                                  f'=custom&sound_index={sound_index}')
                else:
                    logging.error(f'Encountered unexpected type {row["类型"]}')
                trigger = threading.Thread(target=trigger_sender,
                                           args=(device_url, results, i))
                triggers.append(trigger)

            trigger_handler(
                triggers=triggers,
                device_names=devices,
                default_response=f'PLAY command accepted by {devices}',
                results=results)

        if matched is True:
            for i in range(60):
                if stop_signal is False:
                    time.sleep(1)
        else:
            for i in range(30 if debug_mode else 50):
                if stop_signal is False:
                    time.sleep(1)

    return


def main():

    ap = argparse.ArgumentParser()
    ap.add_argument('--debug', dest='debug', action='store_true')
    args = vars(ap.parse_args())
    debug_mode = args['debug']

    global settings
    global app_address, agents_url_list, agent_names_list
    with open(os.path.join(app_dir, 'settings.json'), 'r') as json_file:
        json_str = json_file.read()
        settings = json.loads(json_str)

    app.secret_key = settings['flask']['secret_key']
    app.config['MAX_CONTENT_LENGTH'] = settings['flask']['max_upload_size']
    app_address = settings['app']['address']
    agents_url_list = settings['devices']['urls']
    agent_names_list = settings['devices']['names']
    log_path = settings['app']['log_path']
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

    signal.signal(signal.SIGINT, stop_signal_handler)
    signal.signal(signal.SIGTERM, stop_signal_handler)
    logging.info(f'{app_name} started')

    main_loop_thread = threading.Thread(target=main_loop, args=())
    main_loop_thread.start()
    emailer = importlib.machinery.SourceFileLoader(
                        'emailer',
                        settings['email']['path']).load_module()
    th_email = threading.Thread(target=emailer.send_service_start_notification,
                                kwargs={'settings_path': settings_path,
                                        'service_name': f'{app_name}',
                                        'log_path': log_path,
                                        'delay': 0 if debug_mode else 300})
    th_email.start()

    waitress.serve(app, host="127.0.0.1", port=settings['flask']['port'])

    logging.info(f'{app_name} exited')


if __name__ == '__main__':

    main()
