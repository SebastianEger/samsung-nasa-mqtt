import packetgateway
import os
import tools
import time
import threading 
import argparse
import traceback
import paho.mqtt.client as mqtt
import json
import sys
import signal
from datetime import datetime, timedelta

from nasa_messages import *

from logger import log

def auto_int(x):
  return int(x, 0)

parser = argparse.ArgumentParser()
parser.add_argument('--mqtt-host', default="192.168.0.4", help="host to connect to the MQTT broker")
parser.add_argument('--mqtt-port', default="1883", type=auto_int, help="port of the MQTT broker")
parser.add_argument('--serial-host', default="127.0.0.1",help="host to connect the serial interface endpoint (i.e. socat /dev/ttyUSB0,parenb,raw,echo=0,b9600,nonblock,min=0 tcp-listen:7001,reuseaddr,fork )")
parser.add_argument('--serial-port', default="7001", type=auto_int, help="port to connect the serial interface endpoint")
parser.add_argument('--nasa-interval', default="30", type=auto_int, help="Interval in seconds to republish MQTT values set from the MQTT side (useful for temperature mainly)")
parser.add_argument('--nasa-timeout', default="60", type=auto_int, help="Timeout before considering communication fault")
parser.add_argument('--dump-only', action="store_true", help="Request to only dump packets from the NASA link on the console")
parser.add_argument('--nasa-addr', default="510000", help="Configurable self address to use")
parser.add_argument('--nasa-pnp', action="store_true", help="Perform Plug and Play when set")
args = parser.parse_args()

# NASA state
nasa_state = {}
nasa_fsv_unlocked = False
mqtt_client = None
mqtt_published_vars = {}
pgw = None
last_nasa_rx = time.time()
NASA_PNP_TIMEOUT=30
NASA_PNP_CHECK_INTERVAL=30
NASA_PNP_CHECK_RETRIES=10 # avoid fault on PNP to avoid temp to be messed up and the ASHP to stall
NASA_PNP_RESPONSE_TIMEOUT=10
nasa_pnp_time=0
nasa_pnp_check_retries=0
nasa_pnp_ended=False
nasa_pnp_check_requested=False

def nasa_reset_state():
  global nasa_state
  nasa_state = {}
  log.info("reset NASA state")

class MQTTHandler():
  def __init__(self, mqtt_client, topic, nasa_msgnum):
    self.topic = topic
    self.nasa_msgnum = nasa_msgnum
    self.mqtt_client = mqtt_client
    self.value_int = 0

  def publish(self, valueInt):
    self.value_int = valueInt
    self.mqtt_client.publish(self.topic, valueInt)

  def action(self, client, userdata, msg):
    pass

  def initread(self):
    pass

class IntDiv10MQTTHandler(MQTTHandler):
  def publish(self, valueInt):
    self.value_int = valueInt
    self.mqtt_client.publish(self.topic, valueInt/10.0)

  def action(self, client, userdata, msg):
    intval = int(float(msg.payload.decode('utf-8'))*10)
    while(self.value_int != intval):
      global pgw
      pgw.packet_tx(nasa_set_u16(self.nasa_msgnum, intval))
      time.sleep(5)
      
class IntDiv100MQTTHandler(MQTTHandler):
  def publish(self, valueInt):
    self.value_int = valueInt
    self.mqtt_client.publish(self.topic, valueInt/100.0)

class ONOFFMQTTHandler(MQTTHandler):
  def publish(self, valueInt):
    self.value_int = valueInt
    valueStr = "ON"
    if valueInt==0:
      valueStr="OFF"
    self.mqtt_client.publish(self.topic, valueStr)

class DHWONOFFMQTTHandler(ONOFFMQTTHandler):
  def action(self, client, userdata, msg):
    mqttpayload = msg.payload.decode('utf-8')
    intval=0
    if mqttpayload == "ON":
      intval=1
    while(self.value_int != intval):
      global pgw
      pgw.packet_tx(nasa_dhw_power(intval == 1))
      time.sleep(5)

class Zone1IntDiv10MQTTHandler(IntDiv10MQTTHandler):
  def action(self, client, userdata, msg):
    global nasa_state
    mqttpayload = msg.payload.decode('utf-8')
    self.mqtt_client.publish(self.topic, mqttpayload)
    new_temp = int(float(mqttpayload)*10)
    while(self.value_int != new_temp):
      global pgw
      pgw.packet_tx(nasa_set_zone1_temperature(float(mqttpayload)))
      time.sleep(5)

def ehs_get_mode(default_mode="HOT"):
  global nasa_state
  try:
    # variable holding the current mode
    nasa_name = nasa_message_name(0x4001)
    if not nasa_name in nasa_state:
      return default_mode
    modeint = int(nasa_state[nasa_name])
    if modeint == 0:
      return "AUTO"
    elif modeint == 1:
      return "COLD"
  except:
    pass
  return default_mode

class EHSModeMQTTHandler(MQTTHandler):
  def publish(self, valueInt):
    self.value_int = valueInt
    if valueInt == 0:
      self.mqtt_client.publish(self.topic, "Auto")
    elif valueInt == 1:
      self.mqtt_client.publish(self.topic, "Cold")
    elif valueInt == 4:
      self.mqtt_client.publish(self.topic, "Hot")

  def action(self, client, userdata, msg):
    global pgw
    payload = msg.payload.decode('utf-8')
    if payload == "Auto":
      while(self.value_int != 0):
        pgw.packet_tx(nasa_set_u8(self.nasa_msgnum, 0))
        time.sleep(5)
    elif payload == "Cold":
      while(self.value_int != 1):
        pgw.packet_tx(nasa_set_u8(self.nasa_msgnum, 1))
        time.sleep(5)
    else: # Hot is default
      while(self.value_int != 4):
        pgw.packet_tx(nasa_set_u8(self.nasa_msgnum, 4))
        time.sleep(5)

class Zone1SwitchMQTTHandler(ONOFFMQTTHandler):
  def action(self, client, userdata, msg):
    global nasa_state
    mqttpayload = msg.payload.decode('utf-8')
    global pgw
    enabled = mqttpayload == "ON"
    while(self.value_int != enabled):
      pgw.packet_tx(nasa_zone_power(enabled,1))
      time.sleep(5)

class Zone2IntDiv10MQTTHandler(IntDiv10MQTTHandler):
  def action(self, client, userdata, msg):
    global nasa_state
    mqttpayload = msg.payload.decode('utf-8')
    self.mqtt_client.publish(self.topic, mqttpayload)
    new_temp = int(float(mqttpayload)*10)
    while(self.value_int != new_temp):
      global pgw
      pgw.packet_tx(nasa_set_zone2_temperature(float(mqttpayload)))
      time.sleep(5)
      
class Zone2SwitchMQTTHandler(ONOFFMQTTHandler):
  def action(self, client, userdata, msg):
    global nasa_state
    mqttpayload = msg.payload.decode('utf-8')
    global pgw
    enabled = mqttpayload == "ON"
    while(self.value_int != enabled):
      pgw.packet_tx(nasa_zone_power(enabled,2))
      time.sleep(5)

#handler(source, dest, isInfo, protocolVersion, retryCounter, packetType, payloadType, packetNumber, dataSets)
def rx_nasa_handler(*nargs, **kwargs):
  global mqtt_client
  global last_nasa_rx
  global args
  global pgw
  global nasa_pnp_check_requested
  global nasa_pnp_ended
  last_nasa_rx = time.time()
  packetType = kwargs["packetType"]
  payloadType = kwargs["payloadType"]
  packetNumber = kwargs["packetNumber"]
  dataSets = kwargs["dataSets"]
  source = kwargs["source"]
  dest = kwargs["dest"]
  # ignore non normal packets
  if packetType != "normal":
    log.info("ignoring type of packet")
    return

  # only interpret values from the heatpump, ignore other controllers (especially for the E653 error on overriden zone)
  if source[0]&0xF0 != 0x20 and source[0]&0xF0 != 0x10:
    log.info("ignoring packet from that source")
    return

  # ignore read requests
  if payloadType != "notification" and payloadType != "write" and payloadType != "response":
    log.info("ignoring packet instruction")
    return

  if args.dump_only:
    return

  if args.nasa_pnp:
    # check if PNP packet
    if not nasa_pnp_ended and nasa_is_pnp_phase0_network_address(source, dest, dataSets):
      pgw.packet_tx(nasa_pnp_phase1_request_address(args.nasa_addr))
    elif not nasa_pnp_ended and nasa_is_pnp_phase3_addressing(source, dest, packetNumber, dataSets):
      pgw.packet_tx(nasa_pnp_phase4_ack())
      return
    elif nasa_is_pnp_end(source, dest, dataSets):
      pgw.packet_tx(nasa_poke())
      nasa_pnp_ended = True
      nasa_pnp_check_requested=False
      return

  for ds in dataSets:
    try:
      # we can tag the master's address
      if ( ds[1] == "NASA_IM_MASTER_NOTIFY" and ds[4][0] == 1) or (ds[1] == "NASA_IM_MASTER" and ds[4][0] == 1):
        nasa_state["master_address"] = source

      # detect PNP check's response
      if args.nasa_pnp:
        if nasa_pnp_ended and nasa_pnp_check_requested and payloadType == "response" and tools.bin2hex(source) == "200000" and ds[0] == 0x4229:
          nasa_pnp_check_requested=False

      # hold the value indexed by its name, for easier update of mqtt stuff
      # (set the int raw value)
      nasa_state[ds[1]] = ds[4][0]

      if ds[1] in mqtt_published_vars:
        # use the topic name and payload formatter from the mqtt publish array
        mqtt_p_v = mqtt_published_vars[ds[1]]
        mqtt_p_v.publish(ds[4][0])

      # mqtt_client.publish('homeassistant/sensor/samsung_ehs/nasa_'+hex(ds[0]), payload=ds[2], retain=True)
    except:
      traceback.print_exc()

def rx_event_nasa(p):
  log.debug("packet received "+ tools.bin2hex(p))
  parser.parse_nasa(p, rx_nasa_handler)

#todo: make that parametrized
pgw = packetgateway.PacketGateway(args.serial_host, args.serial_port, rx_event=rx_event_nasa, rxonly=args.dump_only)
parser = packetgateway.NasaPacketParser()
pgw.start()
#ensure gateway is available and publish mqtt is possible when receving values
time.sleep(2)

# once in a while, publish zone2 current temp
def publisher_thread():
  global pgw
  global last_nasa_rx
  global nasa_pnp_time
  global nasa_pnp_check_retries
  global nasa_pnp_check_requested
  global nasa_pnp_ended
  # wait until IOs are setup
  time.sleep(10)
  nasa_last_publish = 0

  if not args.nasa_pnp:
    nasa_set_attributed_address(args.nasa_addr)

  while True:
    try:
      if args.nasa_pnp:
        # start PNP
        if not nasa_pnp_ended or (not nasa_pnp_ended and nasa_pnp_time + NASA_PNP_TIMEOUT < time.time()):
          pgw.packet_tx(nasa_pnp_phase0_request_network_address())
          nasa_pnp_time=time.time()
          nasa_pnp_ended=False
          nasa_pnp_check_requested=False
        # restart PNP?
        if nasa_pnp_ended and nasa_pnp_check_requested and nasa_pnp_time + NASA_PNP_RESPONSE_TIMEOUT < time.time():
          # retry check
          if nasa_pnp_check_retries < NASA_PNP_CHECK_RETRIES:
            pgw.packet_tx(nasa_read_u16(0x4229))
            nasa_pnp_time=time.time()
            nasa_pnp_check_requested=True
          # consider PNP to be redone
          else:
            pgw.packet_tx(nasa_pnp_phase0_request_network_address())
            nasa_pnp_time=time.time()
            nasa_reset_state()
            nasa_pnp_ended=False
            nasa_pnp_check_requested=False
        # detect ASHP reboot and remote controller to execute PNP again
        if nasa_pnp_ended and not nasa_pnp_check_requested and nasa_pnp_time + NASA_PNP_CHECK_INTERVAL < time.time():
          # request reading of MODEL INFORMATION (expect a reponse with it, not the regular notification)
          pgw.packet_tx(nasa_read_u16(0x4229))
          nasa_pnp_time=time.time()
          nasa_pnp_check_retries=0
          nasa_pnp_check_requested=True

    except:
      traceback.print_exc()
    
    # handle communication timeout
    if last_nasa_rx + args.nasa_timeout < time.time():
      log.info("Communication lost!")
      os.kill(os.getpid(), signal.SIGTERM)

    time.sleep(5)

def mqtt_startup_thread():
  global mqtt_client
  def on_connect(client, userdata, flags, rc):
    global nasa_state
    if rc==0:
      mqtt_setup()
      nasa_reset_state()
      pass

  mqtt_client = mqtt.Client(clean_session=True)
  mqtt_client.on_connect=on_connect
  # initial connect may fail if mqtt server is not running
  # post power outage, it may occur the mqtt server is unreachable until
  # after the current script is executed
  while True:
    try:
      mqtt_client.connect(args.mqtt_host, args.mqtt_port)
      mqtt_client.loop_start()
      mqtt_setup()
      break
    except:
      traceback.print_exc()
    time.sleep(1) 

def mqtt_create_topic(nasa_msgnum, topic_config, device_class, name, topic_state, unit_name, type_handler, topic_set, desc_base={}):
  config_content={}
  for k in desc_base:
    config_content[k] = desc_base[k]
  config_content["name"]= name
  topic='notopic'
  if topic_set:
    topic=topic_set
    config_content["command_topic"] = topic_set
  if topic_state:
    topic=topic_state
    config_content["state_topic"] = topic_state
  if device_class:
    config_content["device_class"] = device_class
  if unit_name:
    config_content["unit_of_measurement"] = unit_name

  log.info(topic + " = " + json.dumps(config_content))
  mqtt_client.publish(topic_config, 
    payload=json.dumps(config_content), 
    retain=True)

  nasa_name = nasa_message_name(nasa_msgnum)
  if not nasa_name in mqtt_published_vars:
    handler = type_handler(mqtt_client, topic, nasa_msgnum)
    mqtt_published_vars[nasa_name] = handler
  
  handler = mqtt_published_vars[nasa_name]
  if topic_set:
    mqtt_client.message_callback_add(topic_set, handler.action)
    mqtt_client.subscribe(topic_set)
  
  return handler

def mqtt_setup():
  mqtt_create_topic(0x202, 'homeassistant/sensor/samsung_ehs_error_code_1/config', None, 'Samsung EHS Error Code 1', 'homeassistant/sensor/samsung_ehs_error_code_1/state', None, MQTTHandler, None)

  mqtt_create_topic(0x4427, 'homeassistant/sensor/samsung_ehs_total_output_power/config', 'energy', 'Samsung EHS Total Output Power', 'homeassistant/sensor/samsung_ehs_total_output_power/state', 'Wh', MQTTHandler, None)
  mqtt_create_topic(0x8414, 'homeassistant/sensor/samsung_ehs_total_input_power/config', 'energy', 'Samsung EHS Total Input Power', 'homeassistant/sensor/samsung_ehs_total_input_power/state', 'Wh', MQTTHandler, None)
  
  mqtt_create_topic(0x4426, 'homeassistant/sensor/samsung_ehs_current_output_power/config', 'energy', 'Samsung EHS Output Power', 'homeassistant/sensor/samsung_ehs_current_output_power/state', 'Wh', MQTTHandler, None)
  mqtt_create_topic(0x8413, 'homeassistant/sensor/samsung_ehs_current_input_power/config', 'energy', 'Samsung EHS Input Power', 'homeassistant/sensor/samsung_ehs_current_input_power/state', 'Wh', MQTTHandler, None)
  
  mqtt_create_topic(0x4202, 'homeassistant/sensor/samsung_ehs_temp_water_target/config', 'temperature', 'Samsung EHS Water Target', 'homeassistant/sensor/samsung_ehs_temp_water_target/state', '°C', IntDiv10MQTTHandler, None)
  mqtt_create_topic(0x4236, 'homeassistant/sensor/samsung_ehs_temp_water_in/config', 'temperature', 'Samsung EHS RWT Water In', 'homeassistant/sensor/samsung_ehs_temp_water_in/state', '°C', IntDiv10MQTTHandler, None)
  mqtt_create_topic(0x4238, 'homeassistant/sensor/samsung_ehs_temp_water_out/config', 'temperature', 'Samsung EHS LWT Water Out', 'homeassistant/sensor/samsung_ehs_temp_water_out/state', '°C', IntDiv10MQTTHandler, None)
  mqtt_create_topic(0x420C, 'homeassistant/sensor/samsung_ehs_temp_outer/config', 'temperature', 'Samsung EHS Temp Outer', 'homeassistant/sensor/samsung_ehs_temp_outer/state', '°C', IntDiv10MQTTHandler, None)
  mqtt_create_topic(0x4205, 'homeassistant/sensor/samsung_ehs_temp_eva_in/config', 'temperature', 'Samsung EHS Temp EVA In', 'homeassistant/sensor/samsung_ehs_temp_eva_in/state', '°C', IntDiv10MQTTHandler, None)
  mqtt_create_topic(0x428C, 'homeassistant/sensor/samsung_ehs_temp_mixing_valve_zone1/config', 'temperature', 'Samsung EHS Temp Mixing Valve Zone1', 'homeassistant/sensor/samsung_ehs_temp_mixing_valve_zone1/state', '°C', IntDiv10MQTTHandler, None)
  mqtt_create_topic(0x42E9, 'homeassistant/sensor/samsung_ehs_water_flow/config', 'volume_flow_rate', 'Samsung EHS Water Flow', 'homeassistant/sensor/samsung_ehs_water_flow/state', 'L/min', IntDiv10MQTTHandler, None)
  mqtt_create_topic(0x4028, 'homeassistant/binary_sensor/samsung_ehs_op/config', 'running', 'Samsung EHS Operating', 'homeassistant/binary_sensor/samsung_ehs_op/state', None, ONOFFMQTTHandler, None)
  mqtt_create_topic(0x402E, 'homeassistant/binary_sensor/samsung_ehs_defrosting_op/config', 'running', 'Samsung EHS Defrosting', 'homeassistant/binary_sensor/samsung_ehs_defrosting_op/state', None, ONOFFMQTTHandler, None)
  mqtt_create_topic(0x82FE, 'homeassistant/sensor/samsung_ehs_water_pressure/config', 'pressure', 'Samsung EHS Water Pressure', 'homeassistant/sensor/samsung_ehs_water_pressure/state', 'bar', IntDiv100MQTTHandler, None)
  
  mqtt_create_topic(0x427F, 'homeassistant/sensor/samsung_ehs_temp_water_law_target/config', 'temperature', 'Samsung EHS Temp Water Law Target', 'homeassistant/sensor/samsung_ehs_temp_water_law_target/state', '°C', IntDiv10MQTTHandler, None)

  # EHS mode
  mqtt_create_topic(0x4001, 'homeassistant/select/samsung_ehs_mode/config', None, 'Samsung EHS Mode', 'homeassistant/select/samsung_ehs_mode/state', None, EHSModeMQTTHandler, 'homeassistant/select/samsung_ehs_mode/set', {"options": ["Auto", "Cold", "Hot"]})

  mqtt_create_topic(0x4000, 'homeassistant/switch/samsung_ehs_zone1/config', None, 'Samsung EHS Zone1', 'homeassistant/switch/samsung_ehs_zone1/state', None, Zone1SwitchMQTTHandler, 'homeassistant/switch/samsung_ehs_zone1/set')
  mqtt_create_topic(0x4201, 'homeassistant/number/samsung_ehs_temp_zone1_target/config', 'temperature', 'Samsung EHS Temp Zone1 Target', 'homeassistant/number/samsung_ehs_temp_zone1_target/state', '°C', IntDiv10MQTTHandler, 'homeassistant/number/samsung_ehs_temp_zone1_target/set', {"min": 16, "max": 28, "step": 0.5})
  mqtt_create_topic(0x423A, 'homeassistant/number/samsung_ehs_temp_zone1/config', 'temperature', 'Samsung EHS Temp Zone1', 'homeassistant/number/samsung_ehs_temp_zone1/state', '°C', Zone1IntDiv10MQTTHandler, 'homeassistant/number/samsung_ehs_temp_zone1/set')
  mqtt_create_topic(0x42D8, 'homeassistant/sensor/samsung_ehs_temp_outlet_zone1/config', 'temperature', 'Samsung EHS Temp Outlet Zone1', 'homeassistant/sensor/samsung_ehs_temp_outlet_zone1/state', '°C', IntDiv10MQTTHandler, None)
  
  mqtt_create_topic(0x411e, 'homeassistant/switch/samsung_ehs_zone2/config', None, 'Samsung EHS Zone2', 'homeassistant/switch/samsung_ehs_zone2/state', None, Zone2SwitchMQTTHandler, 'homeassistant/switch/samsung_ehs_zone2/set')
  mqtt_create_topic(0x42D6, 'homeassistant/number/samsung_ehs_temp_zone2_target/config', 'temperature', 'Samsung EHS Temp Zone2 Target', 'homeassistant/number/samsung_ehs_temp_zone2_target/state', '°C', IntDiv10MQTTHandler, 'homeassistant/number/samsung_ehs_temp_zone2_target/set', {"min": 16, "max": 28, "step": 0.5})
  mqtt_create_topic(0x42DA, 'homeassistant/number/samsung_ehs_temp_zone2/config', 'temperature', 'Samsung EHS Temp Zone2', 'homeassistant/number/samsung_ehs_temp_zone2/state', '°C', Zone2IntDiv10MQTTHandler, 'homeassistant/number/samsung_ehs_temp_zone2/set')
  mqtt_create_topic(0x42D9, 'homeassistant/sensor/samsung_ehs_temp_outlet_zone2/config', 'temperature', 'Samsung EHS Temp Outlet Zone2', 'homeassistant/sensor/samsung_ehs_temp_outlet_zone2/state', '°C', IntDiv10MQTTHandler, None)

  mqtt_create_topic(0x4235, 'homeassistant/number/samsung_ehs_temp_dhw_target/config', 'temperature', 'Samsung EHS Temp DHW Target', 'homeassistant/number/samsung_ehs_temp_dhw_target/state', '°C', IntDiv10MQTTHandler, 'homeassistant/number/samsung_ehs_temp_dhw_target/set', {"min": 35, "max": 70, "step": 1})
  mqtt_create_topic(0x4065, 'homeassistant/switch/samsung_ehs_dhw/config', None, 'Samsung EHS DHW', 'homeassistant/switch/samsung_ehs_dhw/state', None, DHWONOFFMQTTHandler, 'homeassistant/switch/samsung_ehs_dhw/set')
  mqtt_create_topic(0x4237, 'homeassistant/sensor/samsung_ehs_temp_dhw/config', 'temperature', 'Samsung EHS Temp DHW Tank', 'homeassistant/sensor/samsung_ehs_temp_dhw/state', '°C', IntDiv10MQTTHandler, None)

  # notify of script start
  topic_state = 'homeassistant/sensor/samsung_ehs_mqtt_bridge/date'
  mqtt_client.publish('homeassistant/sensor/samsung_ehs_mqtt_bridge/config', payload=json.dumps({'state_topic':topic_state,'name':'Samsung EHS Bridge restart date'}), retain=True)
  mqtt_client.publish('homeassistant/sensor/samsung_ehs_mqtt_bridge/date', payload=datetime.strftime(datetime.now(), "%Y%m%d%H%M%S"), retain=True)


threading.Thread(name="publisher", target=publisher_thread).start()
if not args.dump_only:
  threading.Thread(name="mqtt_startup", target=mqtt_startup_thread).start()

log.info("-----------------------------------------------------------------")
log.info("Startup")


"""
TODO:
- detect loss of communication from the ASHP
- test DHW temp after powercycle, to ensure the request is sufficient to persist the value in the Controller




"""
