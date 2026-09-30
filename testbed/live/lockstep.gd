extends Node2D
## A game that steps with others, as a client of a server on a test clock does:
## with --barrier PORT, each frame asks the barrier (barrier.py) for the next
## frame and waits until every instance has asked. Counts the frames it was let
## through, the waits that gave up, and the frames ui_right was held; and
## whether ui_right is down by its events, as a game that tracks its own keys sees it.

const WAIT_MS := 5000

var synced := 0
var timed_out := 0
var held_right := 0
var right_down := false
var _peer: StreamPeerTCP


func _ready() -> void:
	var args := OS.get_cmdline_user_args()
	var at := args.find("--barrier")
	if at < 0 or at + 1 >= args.size():
		return
	_peer = StreamPeerTCP.new()
	_peer.connect_to_host("127.0.0.1", int(args[at + 1]))
	var start := Time.get_ticks_msec()
	while _peer.get_status() == StreamPeerTCP.STATUS_CONNECTING and Time.get_ticks_msec() - start < WAIT_MS:
		_peer.poll()
		OS.delay_msec(1)
	_peer.set_no_delay(true)


func _process(_delta: float) -> void:
	if Input.is_action_pressed("ui_right"):
		held_right += 1
	if _peer == null or _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		return
	_peer.put_data(PackedByteArray([1]))
	var start := Time.get_ticks_msec()
	while Time.get_ticks_msec() - start < WAIT_MS:
		_peer.poll()
		if _peer.get_available_bytes() > 0:
			_peer.get_data(_peer.get_available_bytes())
			synced += 1
			return
		OS.delay_msec(1)
	timed_out += 1


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action("ui_right"):
		right_down = event.is_pressed()
