# The program implements a simple controller for a network with 6 hosts and 5 switches.
# The switches are connected in a diamond topology (without vertical links):
#    - 3 hosts are connected to the left (s1) and 3 to the right (s5) edge of the diamond.
# Overall operation of the controller:
#    - default routing is set in all switches on the reception of packet_in messages form the switch,
#    - then the routing for (h1-h4) pair in switch s1 is changed every one second in a round-robin manner to load balance the traffic through switches s3, s4, s2. 

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.util import dpidToStr
from pox.lib.addresses import IPAddr, EthAddr
from pox.lib.packet.arp import arp
from pox.lib.packet.ethernet import ethernet, ETHER_BROADCAST
from pox.lib.packet.packet_base import packet_base
from pox.lib.packet.packet_utils import *
import pox.lib.packet as pkt
from pox.lib.recoco import Timer
# from enum import Enum
import time
 
log = core.getLogger()
 
s1_dpid=0
s2_dpid=0
s3_dpid=0
s4_dpid=0
s5_dpid=0
 
s1_p1=0
s1_p4=0
s1_p5=0
s1_p6=0
s2_p1=0
s3_p1=0
s4_p1=0
 
pre_s1_p1=0
pre_s1_p4=0
pre_s1_p5=0
pre_s1_p6=0
pre_s2_p1=0
pre_s3_p1=0
pre_s4_p1=0
 
turn=0

#******************************************************************************************************************************************
#******************************************************************************************************************************************
class Link:
  def __init__(self, name, delay=float("inf")):
    self.name = name
    self.delay = delay
    self.connections = 0
    self.balance = 0

delays = {
  'S1-S2': Link('S1-S2'),
  'S1-S3': Link('S1-S3'),
  'S1-S4': Link('S1-S4')
}

class min_delay(object):
    def __init__(self, min_delay=float("inf")):
        self.min_delay = min_delay

class Host(object):
    def __init__(self, name, port):
        self.name = name
        self.port = port

class requested_connection(object):
    def __init__(self, src, dst, min_delay):
        self.src = src
        self.dst = dst
        self.min_delay = min_delay

minimum_delay_for_link1 = min_delay(120)
minimum_delay_for_link2 = min_delay(80)
minimum_delay_for_link3 = min_delay(95)

H1 = Host("H1", 1)
H2 = Host("H2", 2)
H3 = Host("H3", 3)
H4 = Host("H4", 4)
H5 = Host("H5", 5)
H6 = Host("H6", 6)

H1H4 = requested_connection(H1, H4, minimum_delay_for_link1)
H2H5 = requested_connection(H2, H5, minimum_delay_for_link2)
H3H6 = requested_connection(H3, H6, minimum_delay_for_link3)

#******************************************************************************************************************************************
#******************************************************************************************************************************************
start_time = 0.0
sent_time1=0.0
sent_time2=0.0
received_time1 = 0.0
received_time2 = 0.0
src_dpid=0
dst_dpid_s2=0
dst_dpid_s3=0
dst_dpid_s4=0
mytimer = 0
OWD1=0.0
OWD2=0.0
measured_delay_s2=0.0
measured_delay_s3=0.0
measured_delay_s4=0.0


# Get hold of currently measured delay beewteen switches

#probe protocol packet definition; only timestamp field is present in the header (no payload part)
class myproto(packet_base):
  #My Protocol packet struct
  """
  myproto class defines our special type of packet to be sent all the way along including the link between the switches to measure link delays;
  it adds member attribute named timestamp to carry packet creation/sending time by the controller, and defines the 
  function hdr() to return the header of measurement packet (header will contain timestamp)
  """
  #For more info on packet_base class refer to file pox/lib/packet/packet_base.py

  def __init__(self):
     packet_base.__init__(self)
     self.timestamp=0

  def hdr(self, payload):
     return struct.pack('!I', self.timestamp) # code as unsigned int (I), network byte order (!, big-endian - the most significant byte of a word at the smallest memory address)
#******************************************************************************************************************************************
#******************************************************************************************************************************************

def getTheTime():  #function to create a timestamp
  flock = time.localtime()
  then = "[%s-%s-%s" %(str(flock.tm_year),str(flock.tm_mon),str(flock.tm_mday))
 
  if int(flock.tm_hour)<10:
    hrs = "0%s" % (str(flock.tm_hour))
  else:
    hrs = str(flock.tm_hour)
  if int(flock.tm_min)<10:
    mins = "0%s" % (str(flock.tm_min))
  else:
    mins = str(flock.tm_min)
 
  if int(flock.tm_sec)<10:
    secs = "0%s" % (str(flock.tm_sec))
  else:
    secs = str(flock.tm_sec)
 
  then +="]%s.%s.%s" % (hrs,mins,secs)
  return then

def send_probe_packet(src_dpid, src_MAC_addr, dst_dpid, dst_MAC_addr, dst_port):
  global start_time, sent_time1, sent_time2
  if src_dpid <>0 and not core.openflow.getConnection(src_dpid) is None:

    #send out port_stats_request packet through switch0 connection src_dpid (to measure T1)
    core.openflow.getConnection(src_dpid).send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    sent_time1=time.time() * 1000 * 10 - start_time #sending time of stats_req: ctrl => switch0

    #sequence of packet formating operations optimised to reduce the delay variation of e-2-e measurements (to measure T3)
    f = myproto()
    e = pkt.ethernet() #create L2 type packet (frame) object
    e.src = EthAddr(src_MAC_addr)
    e.dst = EthAddr(dst_MAC_addr)
    e.type=0x5577 #set unregistered EtherType in L2 header type field, here assigned to the probe packet type 
    msg = of.ofp_packet_out() #create PACKET_OUT message object
    msg.actions.append(of.ofp_action_output(port=dst_port)) #set the output port for the packet in switch0
    f.timestamp = int(time.time()*1000*10 - start_time) #set the timestamp in the probe packet
    e.payload = f
    msg.data = e.pack()
    core.openflow.getConnection(src_dpid).send(msg)
    # print "=====> probe sent: f=", f.timestamp, " after=", int(time.time()*1000*10 - start_time), " [10*ms]"

  if dst_dpid <>0 and not core.openflow.getConnection(dst_dpid) is None:
    #send out port_stats_request packet through switch1 connection dst_dpid (to measure T2)
    core.openflow.getConnection(dst_dpid).send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
    sent_time2=time.time() * 1000*10 - start_time #sending time of stats_req: ctrl => switch1

def _timer_func ():
  #******************************************************************************************************************************************
  #******************************************************************************************************************************************
  global start_time, sent_time1, sent_time2, src_dpid, dst_dpid_s2, dst_dpid_s3, dst_dpid_s4, current_switch

  send_probe_packet(src_dpid, "1:0:0:0:0:1", dst_dpid_s2, "1:0:0:0:0:2", 4)
  send_probe_packet(src_dpid, "1:0:0:0:0:1", dst_dpid_s3, "1:0:0:0:0:3", 5)
  send_probe_packet(src_dpid, "1:0:0:0:0:1", dst_dpid_s4, "1:0:0:0:0:4", 6)
  #******************************************************************************************************************************************
  #******************************************************************************************************************************************

def _handle_portstats_received (event):
  #******************************************************************************************************************************************
  #******************************************************************************************************************************************

  global s1_dpid, s2_dpid, s3_dpid, s4_dpid, s5_dpid
  global s1_p1,s1_p4, s1_p5, s1_p6, s2_p1, s3_p1, s4_p1
  global pre_s1_p1,pre_s1_p4, pre_s1_p5, pre_s1_p6, pre_s2_p1, pre_s3_p1, pre_s4_p1

  received_time = time.time() * 1000*10 - start_time

  if event.connection.dpid == src_dpid:     
    # print "OWD1 Measured"
    OWD1=0.5*(received_time - sent_time1)   #measure T1 as of lab guide
  elif event.connection.dpid == dst_dpid_s2:   
    OWD2=0.5*(received_time - sent_time2)   #measure T2 as of lab guide
  #******************************************************************************************************************************************
  #******************************************************************************************************************************************
  if event.connection.dpid==s1_dpid: # The DPID of one of the switches involved in the link
    for f in event.stats:
      if int(f.port_no)<65534:
        if f.port_no==1:
          pre_s1_p1=s1_p1
          s1_p1=f.rx_packets
          #print "s1_p1->","TxDrop:", f.tx_dropped,"RxDrop:",f.rx_dropped,"TxErr:",f.tx_errors,"CRC:",f.rx_crc_err,"Coll:",f.collisions,"Tx:",f.tx_packets,"Rx:",f.rx_packets
        if f.port_no==4:
          pre_s1_p4=s1_p4
          s1_p4=f.tx_packets
          #s1_p4=f.tx_bytes
          #print "s1_p4->","TxDrop:", f.tx_dropped,"RxDrop:",f.rx_dropped,"TxErr:",f.tx_errors,"CRC:",f.rx_crc_err,"Coll:",f.collisions,"Tx:",f.tx_packets,"Rx:",f.rx_packets
        if f.port_no==5:
          pre_s1_p5=s1_p5
          s1_p5=f.tx_packets
        if f.port_no==6:
          pre_s1_p6=s1_p6
          s1_p6=f.tx_packets
 
  if event.connection.dpid==s2_dpid:
     for f in event.stats:
       if int(f.port_no)<65534:
         if f.port_no==1:
           pre_s2_p1=s2_p1
           s2_p1=f.rx_packets
           balance = s2_p1 - pre_s2_p1
           delays["S1-S2"].balance = balance
           #s2_p1=f.rx_bytes
           # analyze_portstats_received(s2_dpid, f)
    #  print getTheTime(), "s1_p4(Sent):", (s1_p4-pre_s1_p4), "s2_p1(Received):", (s2_p1-pre_s2_p1)
 
  if event.connection.dpid==s3_dpid:
     for f in event.stats:
       if int(f.port_no)<65534:
         if f.port_no==1:
           pre_s3_p1=s3_p1
           s3_p1=f.rx_packets
           balance = s3_p1 - pre_s3_p1
           delays["S1-S3"].balance = balance
    #  print getTheTime(), "s1_p5(Sent):", (s1_p5-pre_s1_p5), "s3_p1(Received):", (s3_p1-pre_s3_p1)

  if event.connection.dpid==s4_dpid:
     for f in event.stats:
       if int(f.port_no)<65534:
         if f.port_no==1:
           pre_s4_p1=s4_p1
           s4_p1=f.rx_packets
           balance = s4_p1 - pre_s4_p1
           delays["S1-S4"].balance = balance
    # print getTheTime(), "s1_p6(Sent):", (s1_p6-pre_s1_p6), "s4_p1(Received):", (s4_p1-pre_s4_p1)

def _handle_ConnectionUp (event):
  #******************************************************************************************************************************************
  #******************************************************************************************************************************************
  global src_dpid, dst_dpid, mytimer
  #******************************************************************************************************************************************
  #******************************************************************************************************************************************


  # waits for connections from all switches, after connecting to all of them it starts a round robin timer for triggering h1-h4 routing changes
  global s1_dpid, s2_dpid, s3_dpid, s4_dpid, s5_dpid
  global src_dpid, dst_dpid_s2, dst_dpid_s3, dst_dpid_s4
  print "ConnectionUp: ",dpidToStr(event.connection.dpid)
 
  #remember the connection dpid for the switch
  for m in event.connection.features.ports:
    if m.name == "s1-eth1":
      # s1_dpid: the DPID (datapath ID) of switch s1;
      s1_dpid = event.connection.dpid
      src_dpid = event.connection.dpid
      print "s1_dpid=", s1_dpid
    elif m.name == "s2-eth1":
      s2_dpid = event.connection.dpid
      dst_dpid_s2 = event.connection.dpid
      print "s2_dpid=", s2_dpid
    elif m.name == "s3-eth1":
      s3_dpid = event.connection.dpid
      dst_dpid_s3 = event.connection.dpid
      print "s3_dpid=", s3_dpid
    elif m.name == "s4-eth1":
      s4_dpid = event.connection.dpid
      dst_dpid_s4 = event.connection.dpid
      print "s4_dpid=", s4_dpid
    elif m.name == "s5-eth1":
      s5_dpid = event.connection.dpid
      print "s5_dpid=", s5_dpid
 
  # start 1-second recurring loop timer for round-robin routing changes; _timer_func is to be called on timer expiration to change the flow entry in s1
  if s1_dpid<>0 and s2_dpid<>0 and s3_dpid<>0 and s4_dpid<>0 and s5_dpid<>0:
    Timer(1, _timer_func, recurring=True)   # TODO think to when to trigger _timer_func to check network parameters (traffic etc)
 
def howMuchWeHaveDelay(recivedTime, delay, OWD1, OWD2, link):
  global delays
  delayCalculated = int((recivedTime - delay - OWD1 - OWD2 ) / 10) 
  delays[link].delay =  delayCalculated

counter = 0

def _handle_PacketIn(event):
  #******************************************************************************************************************************************
  #******************************************************************************************************************************************
  #This function is called to handle PACKET_IN messages received by the controller
  
  global start_time, OWD1, OWD2, counter, delays

  receivedTime = time.time() * 1000 * 10 - start_time #amount of time elapsed from start_time
 
  packet = event.parsed

  if packet.type==0x5577: #0x5577 is unregistered EtherType, here assigned to probe packets
    counter = counter + 1
    c=packet.find('ethernet').payload
    d,=struct.unpack('!I', c)  # note that d,=... is a struct.unpack and always returns a tuple

    if event.connection.dpid == dst_dpid_s2:
      howMuchWeHaveDelay(receivedTime, d, OWD1, OWD2, 'S1-S2')
    elif event.connection.dpid == dst_dpid_s3:
      howMuchWeHaveDelay(receivedTime, d, OWD1, OWD2, 'S1-S3')
    elif event.connection.dpid == dst_dpid_s4:
      howMuchWeHaveDelay(receivedTime, d, OWD1, OWD2, 'S1-S4')

    if counter % 3 == 0:
      sorted_delays = sorted(delays.items())
      print "Delays: " + ' | '.join("{}: {:<3} [ms]".format(link, delay_value.delay) for link, delay_value in sorted_delays)  
    
    #print "[ms*10]: received_time=", int(received_time), ", d=", d, ", OWD1=", int(OWD1), ", OWD2=", int(OWD2)
    #print "delay:", delay, "[ms] <=====" # divide by 10 to normalise to milliseconds
    return # It is important to analyze only this part when using probe packet, because below code works only for standard packets
  #******************************************************************************************************************************************
  #******************************************************************************************************************************************
   #print "_handle_PacketIn is called, packet.type:", packet.type, " event.connection.dpid:", event.connection.dpid

  # Below, set the default/initial routing rules for all switches and ports.
  # All rules are set up in a given switch on packet_in event received from the switch which means no flow entry has been found in the flow table.
  # This setting up may happen either at the very first pactet being sent or after flow entry expirationn inn the switch
 
  if event.connection.dpid==s1_dpid:
     a=packet.find('arp')					# If packet object does not encapsulate a packet of the type indicated, find() returns None
     if a and a.protodst=="10.0.0.4":
       msg = of.ofp_packet_out(data=event.ofp)			# Create packet_out message; use the incoming packet as the data for the packet out
       msg.actions.append(of.ofp_action_output(port=4))		# Add an action to send to the specified port
       event.connection.send(msg)				# Send message to switch
 
     if a and a.protodst=="10.0.0.5":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=5))
       event.connection.send(msg)
 
     if a and a.protodst=="10.0.0.6":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=6))
       event.connection.send(msg)
 
     if a and a.protodst=="10.0.0.1":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=1))
       event.connection.send(msg)
 
     if a and a.protodst=="10.0.0.2":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=2))
       event.connection.send(msg)
 
     if a and a.protodst=="10.0.0.3":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=3))
       event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800		# rule for IP packets (x0800)
     msg.match.nw_dst = "10.0.0.1"
     msg.actions.append(of.ofp_action_output(port = 1))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.2"
     msg.actions.append(of.ofp_action_output(port = 2))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.3"
     msg.actions.append(of.ofp_action_output(port = 3))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 1
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.4"
     msg.actions.append(of.ofp_action_output(port = 4))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.5"
     msg.actions.append(of.ofp_action_output(port = 5))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.6"
     msg.actions.append(of.ofp_action_output(port = 6))
     event.connection.send(msg)
 
  elif event.connection.dpid==s2_dpid: 
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 1
     msg.match.dl_type=0x0806		# rule for ARP packets (x0806)
     msg.actions.append(of.ofp_action_output(port = 2))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 1
     msg.match.dl_type=0x0800
     msg.actions.append(of.ofp_action_output(port = 2))
     event.connection.send(msg)
  
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 2
     msg.match.dl_type=0x0806
     msg.actions.append(of.ofp_action_output(port = 1))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 2
     msg.match.dl_type=0x0800
     msg.actions.append(of.ofp_action_output(port = 1))
     event.connection.send(msg)
 
  elif event.connection.dpid==s3_dpid: 
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 1
     msg.match.dl_type=0x0806
     msg.actions.append(of.ofp_action_output(port = 2))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 1
     msg.match.dl_type=0x0800
     msg.actions.append(of.ofp_action_output(port = 2))
     event.connection.send(msg)
  
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 2
     msg.match.dl_type=0x0806
     msg.actions.append(of.ofp_action_output(port = 1))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 2
     msg.match.dl_type=0x0800
     msg.actions.append(of.ofp_action_output(port = 1))
     event.connection.send(msg)
  
  elif event.connection.dpid==s4_dpid: 
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 1
     msg.match.dl_type=0x0806
     msg.actions.append(of.ofp_action_output(port = 2))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 1
     msg.match.dl_type=0x0800
     msg.actions.append(of.ofp_action_output(port = 2))
     event.connection.send(msg)
  
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 2
     msg.match.dl_type=0x0806
     msg.actions.append(of.ofp_action_output(port = 1))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 2
     msg.match.dl_type=0x0800
     msg.actions.append(of.ofp_action_output(port = 1))
     event.connection.send(msg)
 
  elif event.connection.dpid==s5_dpid: 
     a=packet.find('arp')
     if a and a.protodst=="10.0.0.4":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=4))
       event.connection.send(msg)
 
     if a and a.protodst=="10.0.0.5":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=5))
       event.connection.send(msg)
 
     if a and a.protodst=="10.0.0.6":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=6))
       event.connection.send(msg)
 
     if a and a.protodst=="10.0.0.1":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=1))
       event.connection.send(msg)
 
     if a and a.protodst=="10.0.0.2":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=2))
       event.connection.send(msg)
 
     if a and a.protodst=="10.0.0.3":
       msg = of.ofp_packet_out(data=event.ofp)
       msg.actions.append(of.ofp_action_output(port=3))
       event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.1"
     msg.actions.append(of.ofp_action_output(port = 1))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =10
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.in_port = 6
     msg.actions.append(of.ofp_action_output(port = 3))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.1"
     msg.actions.append(of.ofp_action_output(port = 1))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.2"
     msg.actions.append(of.ofp_action_output(port = 2))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.3"
     msg.actions.append(of.ofp_action_output(port = 3))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.4"
     msg.actions.append(of.ofp_action_output(port = 4))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.5"
     msg.actions.append(of.ofp_action_output(port = 5))
     event.connection.send(msg)
 
     msg = of.ofp_flow_mod()
     msg.priority =100
     msg.idle_timeout = 0
     msg.hard_timeout = 0
     msg.match.dl_type = 0x0800
     msg.match.nw_dst = "10.0.0.6"
     msg.actions.append(of.ofp_action_output(port = 6))
     event.connection.send(msg)

def setPath(dpid, dstIP, port):
  if dpid<>0:
    msg = of.ofp_flow_mod()
    msg.command=of.OFPFC_MODIFY_STRICT
    msg.priority = 100
    msg.idle_timeout = 0
    msg.hard_timeout = 0
    msg.match.dl_type = 0x0800
    msg.match.nw_dst = str(dstIP)
    msg.actions.append(of.ofp_action_output(port = port+2))
    core.openflow.getConnection(dpid).send(msg)

ALLOWED_MAX_CONN = 2

requested_connections = [H1H4, H2H5, H3H6]

class Test(object):
    def __init__(self, name):
        self.name = name
        self.load = 0

    def increment_load(self):
        self.load += 1

    def display_stats(self):
        print "Link: %s, Set count: %d" % (self.name, self.load)
        


class LinkManager(object):
    def __init__(self):
        self.links = {}

    def add_link(self, link_name):
        if link_name not in self.links:
            self.links[link_name] = Test(link_name)

    def increment_load(self, link_name):
        if link_name in self.links:
            self.links[link_name].increment_load()
        else:
            print "Link not found: %s" % link_name

    def display_stats(self):
        for link in self.links.values():
            link.display_stats()

# Create an instance of LinkManager and add specific links
link_manager = LinkManager()
link_manager.add_link('S1-S2')
link_manager.add_link('S1-S3')
link_manager.add_link('S1-S4')

def theBestLink():
    global requested_connections, s1_dpid, s5_dpid, ALLOWED_MAX_CONN, link_manager

    # Assuming `delays` dictionary contains Link objects from the LinkManager, 
    # or modify to adapt if `delays` structure is different.
    sorted_links = sorted(delays.values(), key=lambda link: link.delay, reverse=True)

    total_balance = float(delays["S1-S2"].balance + delays["S1-S3"].balance + delays["S1-S4"].balance) / 100

    if total_balance != 0:
      delays["S1-S2"].balance = delays["S1-S2"].balance / total_balance
      delays["S1-S3"].balance = delays["S1-S3"].balance / total_balance
      delays["S1-S4"].balance = delays["S1-S4"].balance / total_balance

    for link in sorted_links:
        link.connections = 0  # Reset connections, assuming this is necessary per iteration@

    for request in requested_connections:
        matching_path_found = None
        for link in sorted_links:
            if request.min_delay.min_delay >= link.delay and link.connections < ALLOWED_MAX_CONN:
                matching_path_found = True
                link.connections += 1  # Increment internal counter for some limit check
                
                # Increment load in the LinkManager for this specific link
                link_manager.increment_load(link.name)  # Assuming link.name corresponds to names in LinkManager

                print "Setting path for: %s to %s via %s with delay %.2f" % (request.src.name, request.dst.name, link.name, link.delay)

                destination_ip = "10.0.0.%d" % request.dst.port
                source_ip = "10.0.0.%d" % request.src.port
                link_port = int(link.name.split("-")[1][1:])  # Ensure this splitting logic matches your naming scheme

                setPath(s1_dpid, destination_ip, link_port)
                setPath(s5_dpid, source_ip, link_port - 3)
                break

        if not matching_path_found:
            print "No matching path found for: %s to %s" % (request.src.name, request.dst.name)
    link_manager.display_stats()
    print "Balance: %.2f [%%] | %.2f [%%] | %.2f [%%]" % (delays["S1-S2"].balance, delays["S1-S3"].balance, delays["S1-S4"].balance)
    print "\n"


def launch ():
  global start_time, current_switch
  start_time = time.time() * 1000 * 10
  Timer(1, theBestLink, recurring=True)
  # core is an instance of class POXCore (EventMixin) and it can register objects.
  # An object with name xxx can be registered to core instance which makes this object become a "component" available as pox.core.core.xxx.
  # for examples see e.g. https://noxrepo.github.io/pox-doc/html/#the-openflow-nexus-core-openflow 
  core.openflow.addListenerByName("PortStatsReceived",_handle_portstats_received) # listen for port stats , https://noxrepo.github.io/pox-doc/html/#statistics-events
  core.openflow.addListenerByName("ConnectionUp", _handle_ConnectionUp) # listen for the establishment of a new control channel with a switch, https://noxrepo.github.io/pox-doc/html/#connectionup
  core.openflow.addListenerByName("PacketIn",_handle_PacketIn) # listen for the reception of packet_in message from switch, https://noxrepo.github.io/pox-doc/html/#packetin

