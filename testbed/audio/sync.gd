extends Node2D
## A ball that lands every BOUNCE frames, for gdh live soundsync and gdh measure audio: on the frame it lands,
## `landed` is true and it reports "land" to GdhEvents; Thud plays `delay` frames later (0: on the same frame). Hum
## plays all along. With "--loud" after "--", Thud is 24 dB too loud and clips.

const BOUNCE := 40
const HEIGHT := 200.0

var delay := 0
var landed := false
var ticks := 0
var pending := -1

@onready var ball: Node2D = $Ball
@onready var thud: AudioStreamPlayer = $Thud
@onready var hum: AudioStreamPlayer = $Hum


func _ready() -> void:
	thud.stream = _tone(330.0, 0.12, 0.5)
	hum.stream = _tone(110.0, 1.0, 0.05)
	(hum.stream as AudioStreamWAV).loop_mode = AudioStreamWAV.LOOP_FORWARD
	(hum.stream as AudioStreamWAV).loop_end = 44100
	if "--loud" in OS.get_cmdline_user_args():
		thud.volume_db = 24.0
	hum.play()


func _physics_process(_delta: float) -> void:
	ticks += 1
	var phase := float(ticks % BOUNCE) / BOUNCE
	ball.position.y = 400.0 - HEIGHT * 4.0 * phase * (1.0 - phase)
	landed = ticks % BOUNCE == 0
	if landed:
		GdhEvents.emit("land", ticks)
		pending = delay
	if pending == 0:
		thud.play()
	if pending >= 0:
		pending -= 1


## A sine of `hz` lasting `seconds`, at `level`, as 16-bit PCM at 44.1 kHz.
func _tone(hz: float, seconds: float, level: float) -> AudioStreamWAV:
	var count := int(44100 * seconds)
	var data := PackedByteArray()
	data.resize(count * 2)
	for i in count:
		var fade := minf(1.0, float(count - i) / 400.0)
		data.encode_s16(i * 2, int(sin(TAU * hz * i / 44100.0) * level * fade * 32767))
	var stream := AudioStreamWAV.new()
	stream.format = AudioStreamWAV.FORMAT_16_BITS
	stream.mix_rate = 44100
	stream.data = data
	return stream
