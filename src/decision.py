import time
from dataclasses import dataclass


@dataclass
class Action:
    kind: str
    key: str = None
    duration: float = None

    @classmethod
    def tap(cls, key, duration=None):
        return cls("tap", key, duration)

    @classmethod
    def wait(cls, duration):
        return cls("wait", None, duration)

    def describe(self):
        if self.kind == "wait":
            return f"wait {float(self.duration or 0.0):.3f}s"
        return f"{self.kind} {self.key}"


class CombatBrain:
    def __init__(self, config, humanizer, rng):
        self.config = config
        self.humanizer = humanizer
        self.rng = rng
        combat = config["combat"]
        self.next_attack_at = 0.0
        self.next_potion_at = 0.0
        self.skill_ready_at = {}
        self.hp_history = []
        self.retreat_until = 0.0
        self.search_direction = combat.get("search_direction", "right")
        self.search_until = 0.0

    def reset(self, now=None):
        now = now if now is not None else time.perf_counter()
        self.next_attack_at = now
        self.next_potion_at = now
        self.skill_ready_at.clear()
        self.hp_history.clear()
        self.retreat_until = 0.0
        self.search_until = 0.0

    def update(self, state, now=None):
        if state is None:
            return Action.wait(0.05)
        now = now if now is not None else time.perf_counter()
        combat = self.config["combat"]
        width, height = state["size"]

        hp = state.get("hp")
        danger_action = self._danger_action(hp, now)
        if danger_action is not None:
            return danger_action
        potion_action = self._potion_action(hp, now)
        if potion_action is not None:
            return potion_action

        enemies = state.get("enemies") or []
        player = state.get("player")
        if player is None:
            return Action.wait(0.05)
        if not enemies:
            return self._navigate(now)

        target = self._select_target(enemies, player)
        dx = target["cx"] - player[0]
        dy = target["cy"] - player[1]
        jitter_ratio = float(self.config["humanize"].get("aim_jitter_ratio", 0.0))
        if jitter_ratio > 0:
            dx += self.rng.uniform(-1.0, 1.0) * jitter_ratio * width
            dy += self.rng.uniform(-1.0, 1.0) * jitter_ratio * height

        y_tolerance = float(combat["y_tolerance_ratio"]) * height
        if abs(dy) > y_tolerance:
            hesitation = self.humanizer.maybe_hesitate()
            if hesitation > 0:
                return Action.wait(hesitation)
            key = "down" if dy > 0 else "up"
            duration = self._move_duration(abs(dy) / height, combat["y_move_gain"])
            return Action.tap(key, duration)

        attack_range = float(combat["attack_range_ratio"]) * width
        if abs(dx) > attack_range:
            hesitation = self.humanizer.maybe_hesitate()
            if hesitation > 0:
                return Action.wait(hesitation)
            key = "right" if dx > 0 else "left"
            duration = self._move_duration(abs(dx) / width, combat["x_move_gain"])
            return Action.tap(key, duration)

        return self._attack_action(state, now, dx, dy, width, height)

    def _danger_action(self, hp, now):
        if hp is None:
            return None
        danger = self.config["combat"].get("danger") or {}
        window = float(danger.get("hp_drop_window_s", 1.2))
        self.hp_history.append((now, hp))
        cutoff = now - window
        while self.hp_history and self.hp_history[0][0] < cutoff:
            self.hp_history.pop(0)
        if now < self.retreat_until or len(self.hp_history) < 2:
            return None
        drop = self.hp_history[0][1] - self.hp_history[-1][1]
        if drop < float(danger.get("hp_drop_threshold", 0.12)):
            return None
        key = danger.get("retreat_key")
        self.hp_history.clear()
        if not key:
            return None
        self.retreat_until = now + float(danger.get("retreat_s", 0.5))
        return Action.tap(key, self.humanizer.hold_duration())

    def _potion_action(self, hp, now):
        potion = self.config["combat"].get("potion") or {}
        key = potion.get("key")
        if hp is None or not key:
            return None
        if hp > float(potion.get("hp_threshold", 0.35)):
            return None
        if now < self.next_potion_at:
            return None
        jitter = self.humanizer.seconds(self.config["humanize"].get("cooldown_jitter_s", [0.0, 0.0]))
        self.next_potion_at = now + float(potion.get("cooldown_s", 8.0)) + jitter
        return Action.tap(key, self.humanizer.hold_duration())

    def _navigate(self, now):
        combat = self.config["combat"]
        if now >= self.search_until:
            preferred = combat.get("search_direction", "right")
            other = "left" if preferred == "right" else "right"
            self.search_direction = preferred if self.rng.random() < 0.6 else other
            self.search_until = now + self.rng.uniform(*combat["search_segment_s"])
        hesitation = self.humanizer.maybe_hesitate()
        if hesitation > 0:
            return Action.wait(hesitation)
        jump_key = combat.get("navigate_jump_key")
        if jump_key and self.rng.random() < float(combat.get("navigate_jump_prob", 0.0)):
            return Action.tap(jump_key, self.humanizer.hold_duration())
        duration = self.rng.uniform(*combat["search_step_s"])
        return Action.tap(self.search_direction, duration)

    def _attack_action(self, state, now, dx, dy, width, height):
        combat = self.config["combat"]
        if now < self.next_attack_at:
            return Action.wait(0.02)

        usable = self._usable_skills(state, now, dx, dy, width, height)
        if usable:
            scored = []
            for skill in usable:
                base = max(0.1, float(skill.get("weight", 1.0)))
                scored.append((base * self.rng.uniform(0.85, 1.15), skill))
            scored.sort(key=lambda item: item[0], reverse=True)
            chosen = scored[0][1]
            key = chosen.get("key")
            cooldown = float(chosen.get("cooldown_s", 5.0))
            jitter = self.humanizer.seconds(self.config["humanize"].get("cooldown_jitter_s", [0.0, 0.0]))
            self.skill_ready_at[key] = now + cooldown + jitter
            self.next_attack_at = now + self.humanizer.seconds(combat["attack_gap_s"])
            return Action.tap(key, self.humanizer.hold_duration())

        basic = combat.get("basic_attack") or {}
        basic_key = basic.get("key")
        if basic_key:
            in_range = abs(dx) <= float(basic.get("range_ratio", 0.06)) * width
            aligned = abs(dy) <= float(basic.get("y_tolerance_ratio", 0.03)) * height
            if in_range and aligned:
                self.next_attack_at = now + self.humanizer.seconds(combat["attack_gap_s"])
                return Action.tap(basic_key, self.humanizer.hold_duration())

        return Action.wait(0.02)

    def _usable_skills(self, state, now, dx, dy, width, height):
        slots = state.get("skills") or []
        usable = []
        for skill in self.config["combat"].get("skills", []):
            key = skill.get("key")
            if not key:
                continue
            if now < self.skill_ready_at.get(key, 0.0):
                continue
            slot = skill.get("slot")
            if slots and slot is not None:
                if slot < 0 or slot >= len(slots):
                    continue
                if not slots[slot].get("ready", True):
                    continue
            if abs(dx) > float(skill.get("range_ratio", 0.1)) * width:
                continue
            if abs(dy) > float(skill.get("y_tolerance_ratio", 0.03)) * height:
                continue
            usable.append(skill)
        return usable

    def _select_target(self, enemies, player):
        px, py = player

        def score(enemy):
            return abs(enemy["cx"] - px) + abs(enemy["cy"] - py) * 1.5

        return min(enemies, key=score)

    def _move_duration(self, distance_ratio, gain):
        combat = self.config["combat"]
        duration = float(distance_ratio) * float(gain)
        return max(float(combat["min_move_s"]), min(float(combat["max_move_s"]), duration))
