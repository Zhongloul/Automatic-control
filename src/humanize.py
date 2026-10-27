import random


class Humanizer:
    def __init__(self, config, rng=None):
        self.config = config
        self.rng = rng or random.Random()

    def seconds(self, pair):
        low, high = pair
        if high < low:
            low, high = high, low
        return self.rng.uniform(low, high)

    def milliseconds(self, pair):
        return self.seconds((pair[0] / 1000.0, pair[1] / 1000.0))

    def hold_duration(self):
        return self.milliseconds(self.config["hold_ms"])

    def decision_interval(self):
        return self.milliseconds(self.config["decision_ms"])

    def maybe_hesitate(self):
        if self.rng.random() < self.config.get("hesitate_prob", 0.0):
            return self.milliseconds(self.config["hesitate_ms"])
        return 0.0
