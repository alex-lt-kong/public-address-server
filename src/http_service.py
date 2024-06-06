from typing import Any, Dict, List, Union, Tuple
from flask import Flask, render_template, redirect, Response, request, session

import flask
import datetime as dt
import global_vars as gv
import json
import hashlib
import logging
import os
import pandas as pd
import requests
import shutil
import typing
import threading
import utils
import waitress


app = Flask(__name__)
app.secret_key = b''
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)
app.config['JSON_AS_ASCII'] = False


def validate_uploaded_schedule(path_to_validate: str) -> Tuple[bool, str]:

    try:
        df = pd.read_csv(path_to_validate)
    except Exception as e:
        return False, f'时间表无法解析：{e}'

    if (
        '时' != df.columns[0] or '分' != df.columns[1] or
        '类型' != df.columns[2] or '备注' != df.columns[3]
    ):
        return False, '列名称错误：前四列应依次为[时，分，类型，备注]'

    for name in gv.client_names_list:
        if name not in df.columns:
            return False, f'时间表没有定义设备[{name}]的计划'
        invalid_rows = df.loc[~df[name].isin([0, 1])]
        if invalid_rows.shape[0] == 0:
            continue
        return (
            False,
            f'设备[{name}]的计划的值只能是[0,1]，这些行违反了这个规则：\n\n{invalid_rows.to_string(header=False, index=False)}'
        )

    if len(df.columns) != len(gv.client_names_list) + 4:
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

    if f'{gv.app_name}' in session and 'username' in session[f'{gv.app_name}']:
        pass
    else:
        return Response('未登录', status=400)

    logging.info('uploading new schedule')

    if 'schedule-file' not in request.files:
        return Response('没有接收到文件', 400)
    schedule_file = request.files['schedule-file']

    if schedule_file.filename == '':
        return Response('没有选中的文件', 400)

    filename = 'schedule.csv.tmp'
    playback_history_dir = os.path.dirname(gv.playback_history_file_path)
    temp_file_path = os.path.join(playback_history_dir, filename)
    if os.path.isfile(temp_file_path):
        os.remove(temp_file_path)
    schedule_file.save(temp_file_path)

    retval, message = validate_uploaded_schedule(temp_file_path)

    if retval is False:
        logging.warn(f'Invalidate schedule uploaded: {message}')
        return Response(message.replace('\n', '<br>'), 400)

    try:
        shutil.copy(temp_file_path, gv.schedule_path)
    except Exception:
        return Response('应用时间表错误：无法覆盖现有时间表文件', 500)

    gv.reload_schedule = True
    logging.info('New schedule uploaded and applied')
    return Response('时间表已应用', 200)


@app.route('/download_schedule/', methods=['GET'])
def download_schedule() -> flask.Response:

    if f'{gv.app_name}' in session and 'username' in session[f'{gv.app_name}']:
        pass
    else:
        return Response('未登录', status=400)

    logging.info('Downloading schedule')
    return flask.send_file(
        filename_or_fp=gv.schedule_path,
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

    if f'{gv.app_name}' in session and 'username' in session[f'{gv.app_name}']:
        username = session[f'{gv.app_name}']['username']
    else:
        return Response('未登录', status=400)

    if 'sound_name' in request.args:
        try:
            sound_name = request.args.get('sound_name')
        except Exception as e:
            return Response(f'{e}', status=400)
        if '../' in sound_name or '/..' in sound_name:
            return Response(f'参数[{sound_name}]存在非法字符', status=400)
    else:
        return Response('sound_name not specified', status=400)

    if 'devices' not in request.args:
        return Response('没有选中的播放设备', status=400)
    devices = request.args.get('devices').split(',')
    if len(devices) == 0:
        return Response('没有选中的播放设备', status=400)

    for device in devices:
        if device in gv.client_names_list:
            continue
        return Response(f'[{device}]不在可用设备列表{gv.client_names_list}中', status=400)

    logging.info(f'[{sound_name}] manually played at {devices} by {username}')

    reason = f"""{username}于{str(devices).replace(",", "/").replace("'", "")}播放"""
    utils.update_playback_history(sound_name, reason)

    triggers: List[threading.Thread] = []
    client_resps: List[gv.ClientResponse] = []

    for i in range(len(devices)):
        index = gv.client_names_list.index(devices[i])
        client_resps.append(gv.ClientResponse())
        device_url = (f'{gv.client_urls_list[index]}?sound_name={sound_name}&'
                      f'delay_ms={gv.client_sync_delay_list[i]}')
        triggers.append(threading.Thread(
            target=utils.call_remote_client, args=(device_url, client_resps[-1])))

    return Response(utils.trigger_handler(
        triggers=triggers, device_names=devices, client_resps=client_resps
    ).replace('\n', '<br>'), 200)


@app.route('/client-health-check/', methods=['GET'])
def client_health_check() -> Union[Dict[str, str], flask.Response]:

    if f'{gv.app_name}' in session and 'username' in session[f'{gv.app_name}']:
        # username = session[f'{gv.app_name}']['username']
        pass
    else:
        return Response('未登录', status=400)
    resp: Dict[str, Any] = {}

    for i in range(len(gv.client_urls_list)):
        resp[gv.client_names_list[i]] = {}
        try:
            r = requests.get(
                f'{gv.client_urls_list[i]}health_check/',
                auth=(gv.settings['devices']['username'], gv.settings['devices']['password']),
                timeout=5
            )
            if r.status_code == 200:
                resp[gv.client_names_list[i]]['status'] = '正常'
            else:
                resp[gv.client_names_list[i]]['status'] = '错误'
                resp[gv.client_names_list[i]]['content'] = r.content.decode("utf-8")
                resp[gv.client_names_list[i]]['status_code'] = r.status_code
        except Exception as e:
            resp[gv.client_names_list[i]]['status'] = '错误'
            resp[gv.client_names_list[i]]['content'] = str(e)
            resp[gv.client_names_list[i]]['status_code'] = -1

    return resp


@app.route('/', methods=['GET'])
def index() -> flask.Response:

    if f'{gv.app_name}' in session and 'username' in session[f'{gv.app_name}']:
        username = session[f'{gv.app_name}']['username']
    else:
        return redirect(f'{gv.app_address}/login/')

    playback_items: List[str] = []
    with open(gv.playback_history_file_path, 'r') as f:
        for line in (f.readlines()):
            playback_items.insert(0, line.replace('\n', '').split(','))
    kwargs = {
        'app_address': gv.app_address,
        'playback_items': playback_items,
        'mode': 'dev' if gv.debug_mode else 'prod',
        'agent_names_list': gv.client_names_list,
        'username': username
    }

    return render_template("index.html", **kwargs)


@app.route('/logout/')
def logout() -> flask.Response:

    if f'{gv.app_name}' in session:
        session[f'{gv.app_name}'].pop('username', None)
    return redirect(f'{gv.app_address}/')


@app.before_request
def make_session_permanent() -> None:
    session.permanent = True
    app.permanent_session_lifetime = dt.timedelta(days=90)


@app.route('/login/', methods=['GET', 'POST'])
def login() -> flask.Response:

    if f'{gv.app_name}' in session and 'username' in session[f'{gv.app_name}']:
        return redirect(f'{gv.app_address}/')

    kwargs = {
        'app_address': gv.app_address
    }
    if request.method != 'POST':

        return render_template('login.html', error_message='', **kwargs)

    try:
        with open(gv.users_path, 'r') as json_file:
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
            error_message='错误：密码错误',
            **kwargs)
    session[f'{gv.app_name}'] = {}
    session[f'{gv.app_name}']['username'] = request.form['username']

    return redirect(f'{gv.app_address}/')


def start_http_service():
    app.secret_key = gv.settings['flask']['secret_key']
    app.config['MAX_CONTENT_LENGTH'] = gv.settings['flask']['max_upload_size']

    waitress.serve(app, host="127.0.0.1", port=gv.settings['flask']['port'])
    logging.info("Flask http service exited")
