"""Phase 2.2 — idle personality loop (glances, fidgets, settling, micro-reactions)."""
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from expression.idle_motion import IdleMotion


class _FakeState:
    def __init__(self, mood=0.0, energy=0.7, arousal=0.4, attachment=0.5):
        self.mood, self.energy = mood, energy
        self.arousal, self.attachment = arousal, attachment


class _FakeEyeState:
    def __init__(self, px=0.0, py=0.0):
        self.pupil_x, self.pupil_y = px, py


def _make(energy=0.7, arousal=0.4, curiosity=0.7):
    im = IdleMotion()
    im._running = True
    p = patch("expression.idle_motion.personality")
    pl = patch("expression.idle_motion.personality_learning")
    ee = patch("expression.idle_motion.eye_engine")
    mp, mpl, mee = p.start(), pl.start(), ee.start()
    mp.state = _FakeState(energy=energy, arousal=arousal)
    mpl.get.return_value = curiosity
    mee.event_expression_active = False
    mee.get_state.return_value = _FakeEyeState()
    return im, mp, mpl, mee, (p, pl, ee)


def _stop(patches):
    for p in patches:
        p.stop()


class TestIdleMotion:

    def test_glance_fires_when_due(self):
        im, mp, mpl, mee, patches = _make()
        try:
            now = time.monotonic()
            im._next_glance = now - 1
            im._next_fidget = now + 999
            im._tick(now)
            mee.set_pupil.assert_called_once()
            assert im._next_glance > now
        finally:
            _stop(patches)

    def test_no_motion_when_exhausted(self):
        im, mp, mpl, mee, patches = _make(energy=0.1)
        try:
            now = time.monotonic()
            im._next_glance = now - 1
            im._tick(now)
            mee.set_pupil.assert_not_called()
        finally:
            _stop(patches)

    def test_defers_to_event_expression(self):
        im, mp, mpl, mee, patches = _make()
        try:
            mee.event_expression_active = True
            now = time.monotonic()
            im._next_glance = now - 1
            im._tick(now)
            mee.set_pupil.assert_not_called()
        finally:
            _stop(patches)

    def test_fidget_only_when_energy_sags(self):
        im, mp, mpl, mee, patches = _make(energy=0.4, arousal=0.3)
        try:
            now = time.monotonic()
            im._next_glance = now + 999
            im._next_fidget = now - 1
            im._tick(now)
            mee.set_pupil.assert_called_once()
        finally:
            _stop(patches)

    def test_no_fidget_when_energetic(self):
        im, mp, mpl, mee, patches = _make(energy=0.9, arousal=0.3)
        try:
            now = time.monotonic()
            im._next_glance = now + 999
            im._next_fidget = now - 1
            im._tick(now)
            mee.set_pupil.assert_not_called()
        finally:
            _stop(patches)

    def test_settling_eases_pupils_to_center(self):
        im, mp, mpl, mee, patches = _make()
        try:
            st = _FakeEyeState(px=0.9, py=-0.6)
            mee.get_state.return_value = st
            now = time.monotonic()
            im._next_glance = now + 999
            im._next_fidget = now + 999
            im._hold_until = now - 1
            im._tick(now)
            assert abs(st.pupil_x) < 0.9
            assert abs(st.pupil_y) < 0.6
            for _ in range(50):
                im._tick(now)
            assert st.pupil_x == 0.0 and st.pupil_y == 0.0
        finally:
            _stop(patches)

    def test_micro_react_darts_pupils(self):
        im, mp, mpl, mee, patches = _make()
        try:
            im._micro_react(x=0.8, y=-0.2)
            mee.set_pupil.assert_called_once_with(0.8, -0.2)
            assert im._hold_until > time.monotonic()
        finally:
            _stop(patches)

    def test_curious_aroused_glances_more_often(self):
        im_hi, _, _, _, p1 = _make(arousal=0.9, curiosity=0.95)
        try:
            hi = sum(im_hi._glance_interval() for _ in range(50))
        finally:
            _stop(p1)
        im_lo, _, _, _, p2 = _make(arousal=0.1, curiosity=0.3)
        try:
            lo = sum(im_lo._glance_interval() for _ in range(50))
        finally:
            _stop(p2)
        assert hi < lo
