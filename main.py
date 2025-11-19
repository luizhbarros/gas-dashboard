import streamlit as st
import paho.mqtt.client as mqtt
import threading
import time
import pandas as pd
from datetime import datetime
import altair as alt
import requests
import urllib.parse


TOPIC_LPG   = "railtracker/gas/lpg_ppm"
TOPIC_ALERT = "railtracker/gas/alert"
MQTT_BROKER = st.secrets["MQTT_BROKER"]
MQTT_PORT   = int(st.secrets["MQTT_PORT"])
MQTT_USER   = st.secrets["MQTT_USER"]
MQTT_PASS   = st.secrets["MQTT_PASS"]

# WhatsApp (CallMeBot)
CALLMEBOT_PHONE = "5511994109391"
CALLMEBOT_KEY   = "9991859"

# variável global pra guardar último valor
latest_ppm = 0.0
latest_ts  = "-"   # timestamp da última leitura

# lista para histórico de leituras (elapsed_s + ppm)
readings = []

# histórico de alertas
alerts = []
last_alert_ppm = 0.0
last_alert_ts = "-"
alert_update_id = 0

# log de mensagens enviadas ao WhatsApp
telegram_log = []

# contador de atualizações (pra saber quando chegou dado novo)
last_update_id = 0

# contador de amostras (cada amostra = 20 s)
sample_index = 0

# guarda o último patamar de qualidade do ar ("verde", "amarelo", "vermelho")
last_status_level = None

prev_ppm = None  # último valor de LPG para cálculo de delta no st.metric


def send_whatsapp(text: str):
    """Envia mensagem via CallMeBot e registra no log local."""
    # registra no log local para aparecer no dashboard
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    telegram_log.append({"Horário": timestamp, "Mensagem": text})

    # envia mensagem via CallMeBot
    msg = urllib.parse.quote(text)
    url = f"https://api.callmebot.com/whatsapp.php?phone={CALLMEBOT_PHONE}&text={msg}&apikey={CALLMEBOT_KEY}"

    try:
        r = requests.get(url, timeout=5)
        status_info = f"HTTP {r.status_code} - {r.text[:200]}"
        print("WhatsApp resp:", status_info)

        # também guarda no log visível (debug)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        telegram_log.append({"Horário": timestamp, "Mensagem": text + f"\n\n[DEBUG] {status_info}"})

    except Exception as e:
        err_info = f"Erro WhatsApp: {e}"
        print(err_info)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        telegram_log.append({"Horário": timestamp, "Mensagem": text + f"\n\n[DEBUG] {err_info}"})


def on_connect(client, userdata, flags, rc):
    print("Conectado ao MQTT com código", rc)
    client.subscribe([(TOPIC_LPG, 0), (TOPIC_ALERT, 0)])


def on_message(client, userdata, msg):
    global latest_ppm, last_update_id
    global last_alert_ppm, last_alert_ts, alert_update_id

    try:
        payload_str = msg.payload.decode("utf-8")
        value = float(payload_str)
    except Exception:
        return

    if msg.topic == TOPIC_LPG:
        latest_ppm = value
        last_update_id += 1

    elif msg.topic == TOPIC_ALERT:
        last_alert_ppm = value
        last_alert_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        alert_update_id += 1


def mqtt_thread():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    # autenticação
    client.username_pw_set(MQTT_USER, MQTT_PASS)

    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_forever()


# inicia o thread do MQTT
threading.Thread(target=mqtt_thread, daemon=True).start()

# --- UI do Streamlit ---
st.set_page_config(page_title="Gas Monitor", page_icon="🧪", layout="centered")

st.title("Gas Monitor - LPG (MQ-2)")
st.markdown("Leitura em tempo real a partir do Mosquitto")

top_col1, top_col2, top_col3 = st.columns(3)
card_ppm = top_col1.empty()
card_status = top_col2.empty()
card_last_alert = top_col3.empty()

st.markdown("### Histórico de leituras")
chart_placeholder = st.empty()  # placeholder para o gráfico

st.markdown("### Histórico de alertas")
alerts_table_placeholder = st.empty()

st.markdown("### Log do Whatsapp")
telegram_log_placeholder = st.empty()

# guarda qual update_id já foi processado
last_processed_id = -1

# mensagem de teste inicial
send_whatsapp("Teste manual do dashboard: se você recebeu isso, o CallMeBot está OK.")

while True:
    # só atualiza se chegou leitura nova via MQTT
    if last_update_id != last_processed_id:
        last_processed_id = last_update_id

        ppm = latest_ppm

        # cada nova leitura = +20s no eixo X
        sample_index += 1
        elapsed_s = sample_index * 20

        # timestamp real da leitura
        latest_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # registra no histórico: eixo X = segundos acumulados
        readings.append({"elapsed_s": elapsed_s, "ppm": ppm})

        # mantém tamanho máximo (ex: últimas 200 leituras)
        if len(readings) > 200:
            readings = readings[-200:]

        # cria DataFrame para gráfico (eixo X numérico: 0,20,40,...)
        df = pd.DataFrame(readings)

        chart = (
            alt.Chart(df)
            .mark_circle(size=80)
            .encode(
                x=alt.X("elapsed_s:Q", title="Tempo (s) – cada ponto = 20s"),
                y=alt.Y("ppm:Q", title="LPG (ppm)"),
                tooltip=["elapsed_s", "ppm"]
            )
            .interactive()
        )

        chart_placeholder.altair_chart(chart, use_container_width=True)

        # lógica de status
        if ppm <= 1000:
            status = "🟢 Seguro"
            status_level = "verde"
        elif ppm <= 2000:
            status = "🟡 Atenção"
            status_level = "amarelo"
        else:
            status = "🔴 Perigo"
            status_level = "vermelho"

        # ===== Lógica de envio de alerta quando mudar de patamar =====
        if last_status_level is None:
            # primeira leitura: se já entrou em vermelho, também dispara alerta
            if status_level == "vermelho":
                msg = (
                    "⚠️ Qualidade do ar entrou em nível CRÍTICO!\n\n"
                    f"De: NENHUM\n"
                    f"Para: {status_level.upper()}\n"
                    f"LPG: {ppm:.2f} ppm\n"
                    f"Horário: {latest_ts}"
                )
                send_whatsapp(msg)
            last_status_level = status_level
        elif status_level != last_status_level:
            # mudou de patamar (verde -> amarelo, amarelo -> vermelho, etc.)
            msg = (
                "⚠️ Qualidade do ar mudou de patamar!\n\n"
                f"De: {last_status_level.upper()}\n"
                f"Para: {status_level.upper()}\n"
                f"LPG: {ppm:.2f} ppm\n"
                f"Horário: {latest_ts}"
            )
            send_whatsapp(msg)
            last_status_level = status_level

        # --- MÉTRICAS (st.metric) ---

        # LPG atual com delta em relação à última leitura
        if prev_ppm is None:
            delta_ppm = 0.0
        else:
            delta_ppm = ppm - prev_ppm

        card_ppm.metric(
            label="LPG Atual (ppm)",
            value=f"{ppm:.2f}",
            delta=f"{delta_ppm:+.2f} ppm",
        )

        # guarda valor atual para próxima comparação
        prev_ppm = ppm

        # Status textual, sem duplicar a palavra (ex.: "Seguro Seguro")
        if status_level == "verde":
            status_text = "Seguro"
        elif status_level == "amarelo":
            status_text = "Atenção"
        else:
            status_text = "Perigo"

        card_status.metric(
            label="Status",
            value=status_text,
        )

        # Último alerta (se já houve algum)
        if last_alert_ts != "-":
            card_last_alert.metric(
                label="Último alerta (ppm)",
                value=f"{last_alert_ppm:.2f}",
                delta=last_alert_ts,
            )
        else:
            card_last_alert.metric(
                label="Último alerta (ppm)",
                value="--",
                delta="Sem alertas",
            )

    # Bloco de processamento de alertas (TOPIC_ALERT)
    if alert_update_id > 0:
        # Registrar alerta novo
        alerts.append({"Timestamp": last_alert_ts, "PPM": last_alert_ppm})
        if len(alerts) > 200:
            alerts = alerts[-200:]

        # Envio de alerta via WhatsApp sempre que um novo alerta MQTT chegar
        msg = (
            "🚨 ALERTA DE GÁS DETECTADO 🚨\n\n"
            f"LPG: {last_alert_ppm:.2f} ppm\n"
            f"Horário: {last_alert_ts}"
        )
        send_whatsapp(msg)

        # Atualiza tabela de alertas com st.table (sem índice numérico)
        df_alerts = pd.DataFrame(alerts)
        df_alerts = df_alerts.reset_index(drop=True)
        st.markdown("""
        <style>
        thead tr th:first-child {width: 200px !important;}
        tbody td {white-space: nowrap !important;}
        </style>
        """, unsafe_allow_html=True)
        alerts_table_placeholder.table(df_alerts)

        # Atualiza log de WhatsApp com st.table (sem índice numérico)
        if len(telegram_log) > 0:
            # Sanitizar logs para evitar quebras de linha
            clean_log = []
            for item in telegram_log:
                clean_log.append({
                    "Horário": str(item["Horário"]).replace("\n", " ").strip(),
                    "Mensagem": str(item["Mensagem"]).replace("\n", " ").replace("<br>", " ").strip()
                })

            df_log = pd.DataFrame(clean_log)
            df_log = df_log.reset_index(drop=True)
            st.markdown("""
            <style>
            thead tr th:first-child {width: 200px !important;}
            tbody td {white-space: nowrap !important;}
            </style>
            """, unsafe_allow_html=True)
            telegram_log_placeholder.table(df_log)

        # reset update flag
        alert_update_id = 0

    # dorme um pouco só pra não fritar CPU
    time.sleep(0.1)