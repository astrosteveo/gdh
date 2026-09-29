extends Control

@export var max_health := 100
@export var health := 85
@export var max_mana := 60.0
@export var mana := 42.0
@export var score := 12450
@export var gold := 327
@export var quest_title := "The Old Mill"
@export var objectives: Array[String] = ["Defeat the slimes (3/5)", "Find the cellar key"]

@onready var health_bar: ProgressBar = $StatusPanel/Bars/HealthBar
@onready var health_label: Label = $StatusPanel/Bars/HealthBar/Value
@onready var mana_bar: ProgressBar = $StatusPanel/Bars/ManaBar
@onready var mana_label: Label = $StatusPanel/Bars/ManaBar/Value
@onready var score_label: Label = $ScorePanel/Score
@onready var gold_label: Label = $ScorePanel/GoldAmount
@onready var quest_title_label: Label = $QuestPanel/Title
@onready var objective_template: Label = $QuestPanel/Margin/Rows/Objective


func _ready() -> void:
	_refresh_status()
	_refresh_score()
	_refresh_quest()


func _refresh_status() -> void:
	health_bar.value = health / max_health * 100
	health_label.text = "%d / %d" % [health, max_health]
	mana_bar.value = mana / max_mana * 100
	mana_label.text = "%d / %d" % [mana, max_mana]


func _refresh_score() -> void:
	score_label.text = "%07d" % score
	gold_label.text = str(gold)


func _refresh_quest() -> void:
	quest_title_label.text = quest_title
	for i in objectives.size():
		var row: Label = objective_template if i == 0 else objective_template.duplicate()
		row.text = "- " + objectives[i]
		if i > 0:
			objective_template.get_parent().add_child(row)
