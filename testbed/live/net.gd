extends Node
## A game that talks to a server (echo.py) over UDP and TCP at --port PORT, as a networked game does, for the
## network proxy's tests: a numbered UDP ping a frame, whose echo gives a round trip time, and a byte a frame over a
## TCP connection, made again whenever it ends.

var udp := PacketPeerUDP.new()
var tcp := StreamPeerTCP.new()
var port := 0
var udp_replies := 0
## Milliseconds from the latest answered ping to its answer (-1 before any).
var last_rtt_ms := -1
var tcp_connects := 0
var tcp_drops := 0
var tcp_replies := 0
var _sent := 0
var _sent_at := {}
var _connected := false


func _ready() -> void:
	var args := OS.get_cmdline_user_args()
	port = int(args[args.find("--port") + 1])
	udp.connect_to_host("127.0.0.1", port)
	tcp.connect_to_host("127.0.0.1", port)


func _process(_delta: float) -> void:
	_sent_at[_sent] = Time.get_ticks_msec()
	udp.put_packet(str(_sent).to_utf8_buffer())
	_sent += 1
	while udp.get_available_packet_count() > 0:
		var seq := int(udp.get_packet().get_string_from_utf8())
		udp_replies += 1
		last_rtt_ms = Time.get_ticks_msec() - int(_sent_at.get(seq, 0))
		_sent_at.erase(seq)
	tcp.poll()
	match tcp.get_status():
		StreamPeerTCP.STATUS_CONNECTED:
			if not _connected:
				_connected = true
				tcp_connects += 1
			tcp.put_data("x".to_utf8_buffer())
			var waiting := tcp.get_available_bytes()
			if waiting > 0:
				tcp.get_data(waiting)
				tcp_replies += waiting
		StreamPeerTCP.STATUS_ERROR, StreamPeerTCP.STATUS_NONE:
			if _connected:
				_connected = false
				tcp_drops += 1
			tcp = StreamPeerTCP.new()
			tcp.connect_to_host("127.0.0.1", port)


## Counts from now on.
func reset() -> void:
	udp_replies = 0
	tcp_replies = 0
	last_rtt_ms = -1
