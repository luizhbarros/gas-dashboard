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
CALLMEBOT_PHONE = "5511994109391"
CALLMEBOT_KEY = "9991859"

# variável global pra guardar último valor
latest_ppm = 0.0
latest_ts  = "-"   # timestamp da última leitura

# lista para histórico de leituras (elapsed_s + ppm)
readings = []
alerts = []          # histórico de alertas
last_alert_ppm = 0.0
last_alert_ts = "-"
alert_update_id = 0
telegram_log = []

# contador de atualizações (pra saber quando chegou dado novo)
last_update_id = 0

# contador de amostras (cada amostra = 20 s)
sample_index = 0
# guarda o último patamar de qualidade do ar ("verde", "amarelo", "vermelho")
last_status_level = None

def send_whatsapp(text):
    msg = urllib.parse.quote(text)
    url = f"https://api.callmebot.com/whatsapp.php?phone={CALLMEBOT_PHONE}&text={msg}&apikey={CALLMEBOT_KEY}"

    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            print("Erro WhatsApp:", r.text)
    except Exception as e:
        print("Erro WhatsApp:", e)


def on_connect(client, userdata, flags, rc):
    print("Conectado ao MQTT com código", rc)
    client.subscribe([(TOPIC_LPG, 0), (TOPIC_ALERT, 0)])


def on_message(client, userdata, msg):
    global latest_ppm, last_update_id
    global last_alert_ppm, last_alert_ts, alert_update_id

    try:
        payload_str = msg.payload.decode("utf-8")
        value = float(payload_str)
    except:
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

while True:
    # só atualiza se chegou leitura nova via MQTT
    if last_update_id != last_processed_id:
        last_processed_id = last_update_id

        ppm = latest_ppm

        # cada nova leitura = +20s no eixo X
        sample_index += 1
        elapsed_s = sample_index * 20

        # timestamp real da leitura (mostrado só na janela de última leitura)
        latest_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # registra no histórico: eixo X = segundos acumulados
        readings.append({"elapsed_s": elapsed_s, "ppm": ppm})

        # mantém tamanho máximo (ex: últimas 200 leituras)
        if len(readings) > 200:
            readings = readings[-200:]

        # cria DataFrame para gráfico (eixo X numérico: 0,20,40,...)
        df = pd.DataFrame(readings)

        # label de tempo em segundos (20, 40, 60...) como string
        df["tempo_s"] = df["elapsed_s"].astype(int).astype(str)

        chart = (
            alt.Chart(df)
            .mark_line(point=True)
            .encode(
                x=alt.X("tempo_s:O", title="Tempo (s) – cada ponto = 20s"),  # O = ordinal
                y=alt.Y("ppm:Q", title="LPG (ppm)"),
                tooltip=["tempo_s", "ppm"]
            )
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
            # primeira leitura: só inicializa
            last_status_level = status_level
        elif status_level != last_status_level:
            # mudou de patamar (verde -> amarelo, amarelo -> vermelho, etc.)
            msg = (
                f"⚠️ Qualidade do ar mudou de patamar!\n\n"
                f"*De:* {last_status_level.upper()}\n"
                f"*Para:* {status_level.upper()}\n"
                f"*LPG:* {ppm:.2f} ppm\n"
                f"*Horário:* {latest_ts}"
            )
            send_whatsapp(msg)
            last_status_level = status_level

        # card de LPG atual
        card_ppm.markdown(
            f"""
            <div style="padding:1rem;border-radius:0.75rem;background-color:#111827;
                        border:1px solid #374151;">
              <div style="font-size:0.8rem;color:#9CA3AF;">LPG atual</div>
              <div style="font-size:1.8rem;font-weight:700;color:#E5E7EB;">{ppm:.2f} ppm</div>
              <div style="font-size:0.75rem;color:#6B7280;margin-top:0.25rem;">Última leitura: {latest_ts}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # card de status
        card_status.markdown(
            f"""
            <div style="padding:1rem;border-radius:0.75rem;background-color:#111827;
                        border:1px solid #374151;">
              <div style="font-size:0.8rem;color:#9CA3AF;">Status</div>
              <div style="font-size:1.5rem;font-weight:600;color:#E5E7EB;margin-top:0.25rem;">{status}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # dorme um pouco só pra não fritar CPU; não redesenha se não tiver leitura nova
    if alert_update_id > 0:
        # Registrar alerta novo
        alerts.append({"Tempo": last_alert_ts, "PPM": last_alert_ppm})
        if len(alerts) > 200:
            alerts = alerts[-200:]

        # Último alerta em card
        date_only = last_alert_ts.split(" ")[0]
        card_last_alert.markdown(
            f"""
            <div style="padding:1rem;border-radius:0.75rem;background-color:#111827;
                        border:1px solid #4B5563;">
              <div style="font-size:0.8rem;color:#9CA3AF;">Último alerta</div>
              <div style="font-size:0.95rem;color:#F9FAFB;margin-top:0.25rem;">Data: {date_only}</div>
              <div style="font-size:0.95rem;color:#F9FAFB;">PPM: {last_alert_ppm:.2f}</div>
              <div style="font-size:0.75rem;color:#6B7280;margin-top:0.25rem;">Timestamp: {last_alert_ts}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Tabela de histórico
        df_alerts = pd.DataFrame(alerts)
        alerts_table_placeholder.dataframe(df_alerts, use_container_width=True)

        if len(telegram_log) > 0:
            df_telegram = pd.DataFrame(telegram_log)
            telegram_log_placeholder.dataframe(df_telegram, use_container_width=True)

        # reset update flag
        alert_update_id = 0
    time.sleep(0.1)