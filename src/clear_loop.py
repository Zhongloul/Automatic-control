import threading
import time
from dataclasses import dataclass


@dataclass
class ClearLoopResult:
    success: bool
    message: str
    loops: int = 0
    navigations: int = 0
    elapsed: float = 0.0


class ClearLoopRunner:
    def __init__(
        self,
        combat_phase,
        navigate_phase,
        loops=0,
        interval_s=2.0,
        nav_retries=1,
        abort_event=None,
        log=None,
        progress=None,
    ):
        self.combat_phase = combat_phase
        self.navigate_phase = navigate_phase
        self.loops = max(0, int(loops))
        self.interval_s = max(0.0, float(interval_s))
        self.nav_retries = max(0, int(nav_retries))
        self.abort_event = abort_event or threading.Event()
        self.log = log or (lambda message: None)
        self.progress = progress or (lambda *args: None)

    def _aborted(self):
        return self.abort_event.is_set()

    def _sleep(self, seconds):
        end = time.perf_counter() + max(0.0, float(seconds))
        while True:
            if self._aborted():
                return False
            remaining = end - time.perf_counter()
            if remaining <= 0:
                return True
            time.sleep(min(0.05, remaining))

    def run(self):
        start = time.perf_counter()
        loops_done = 0
        navigations = 0
        while self.loops == 0 or loops_done < self.loops:
            if self._aborted():
                return ClearLoopResult(False, "循环流程已停止", loops_done, navigations, time.perf_counter() - start)
            self.progress(loops_done + 1, self.loops, "combat")
            ok, message = self.combat_phase()
            if not ok:
                return ClearLoopResult(
                    False,
                    message or "清图阶段失败",
                    loops_done,
                    navigations,
                    time.perf_counter() - start,
                )
            attempts = self.nav_retries + 1
            navigated = False
            last_message = ""
            for attempt in range(1, attempts + 1):
                if self._aborted():
                    return ClearLoopResult(False, "循环流程已停止", loops_done, navigations, time.perf_counter() - start)
                self.progress(loops_done + 1, self.loops, "navigate")
                ok, last_message = self.navigate_phase()
                if ok:
                    navigated = True
                    break
                self.log(f"导航失败（第 {attempt}/{attempts} 次）: {last_message}")
                if attempt < attempts and not self._sleep(1.0):
                    return ClearLoopResult(False, "循环流程已停止", loops_done, navigations, time.perf_counter() - start)
            if not navigated:
                return ClearLoopResult(
                    False,
                    last_message or "导航阶段失败",
                    loops_done,
                    navigations,
                    time.perf_counter() - start,
                )
            navigations += 1
            loops_done += 1
            if self.loops and loops_done >= self.loops:
                break
            if not self._sleep(self.interval_s):
                return ClearLoopResult(False, "循环流程已停止", loops_done, navigations, time.perf_counter() - start)
        return ClearLoopResult(
            True,
            f"循环完成（{loops_done} 轮）",
            loops_done,
            navigations,
            time.perf_counter() - start,
        )
