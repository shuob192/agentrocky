"""
Main floating Rocky window.

Coordinate notes (Qt):
  - (0,0) is screen top-left; y increases downward.
  - pos_x, pos_y = top-left corner of the window in global screen coords.
  - "bottom wall" = window bottom touches screen bottom → pos_y = max_y.
  - velocity_y > 0 → moving downward (falling); < 0 → moving upward (jumping).
  - Gravity is +980 (pulling downward).
"""

import math
import os
import random
import subprocess

from PyQt6.QtCore import (
    QPoint, QRect, QTimer, Qt, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QPainter, QPainterPath,
    QPen, QPixmap, QTransform,
)
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from rocky_state import RockyState, RockyWall

ROCKY_W = 180
ROCKY_H = 140
SPRITE_SIZE = 80

# Physics constants
BASE_WALK_SPEED = 100.0
JUMP_SPEED = 420.0
FALL_GRAVITY = 980.0
PARACHUTE_GRAVITY = 260.0
PARACHUTE_TERMINAL = 150.0    # max fall speed (positive, downward)
PARACHUTE_DEPLOY = 210.0      # deploy when vy exceeds this

CURSOR_NOTICE_DIST = 280.0
CURSOR_FACING_MIN = 150.0
CURSOR_FACING_AXIS = 56.0
CURSOR_FACING_COOLDOWN = 0.35  # seconds

WORKING_MESSAGES = ['rocky building', 'rocky do big science', 'rocky save erid']
JAZZ_MESSAGES    = ['fist my bump', 'amaze amaze amaze', 'rocky hate mark']


class RockyWindow(QWidget):
    def __init__(self, session, assets_dir):
        super().__init__()
        self.session = session
        self.assets_dir = assets_dir
        self.state = RockyState()

        self._current_walk_speed = BASE_WALK_SPEED
        self._last_tick_ms = 0
        self._last_cursor_facing_ms = 0
        self._speech_bubble_timer = None
        self._chat_window = None

        self._drag_start_global = None
        self._drag_start_win_pos = None
        self._has_dragged = False
        DRAG_THRESHOLD = 4
        self._drag_threshold_sq = DRAG_THRESHOLD ** 2

        self._load_sprites()
        self._setup_window()
        self._setup_screen_bounds()
        self._setup_timers()
        self._connect_session()
        self._schedule_random_jazz()
        self._schedule_random_jump(first=True)
        self._schedule_random_decision(first=True)
        self._schedule_random_look_around(first=True)

    # ----------------------------------------------------------------- setup

    def _load_sprites(self):
        names = ['stand', 'walkleft1', 'walkleft2', 'jazz1', 'jazz2', 'jazz3']
        self._sprites = {}
        for name in names:
            path = os.path.join(self.assets_dir, f'{name}.png')
            px = QPixmap(path)
            if px.isNull():
                px = None
            else:
                px = px.scaled(SPRITE_SIZE, SPRITE_SIZE, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.FastTransformation)
            self._sprites[name] = px

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(ROCKY_W, ROCKY_H)

    def _setup_screen_bounds(self):
        screen = QApplication.primaryScreen()
        geom = screen.availableGeometry()
        self.state.min_x = float(geom.x())
        self.state.max_x = float(geom.x() + geom.width() - ROCKY_W)
        self.state.min_y = float(geom.y())
        self.state.max_y = float(geom.y() + geom.height() - ROCKY_H)
        # Start at bottom-center
        self.state.pos_x = (geom.x() + geom.width() - ROCKY_W) / 2.0
        self.state.pos_y = self.state.max_y
        self.state.wall = RockyWall.BOTTOM
        self.move(int(self.state.pos_x), int(self.state.pos_y))
        self.show()

    def _setup_timers(self):
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._update_position)
        self._tick_timer.start(1000 // 60)

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._update_frame)
        self._frame_timer.start(1000 // 8)

        self._cursor_timer = QTimer(self)
        self._cursor_timer.timeout.connect(self._update_cursor)
        self._cursor_timer.start(1000 // 12)

        from PyQt6.QtCore import QTime
        self._last_tick_ms = QTime.currentTime().msecsSinceStartOfDay()

    def _connect_session(self):
        self._prev_running = False
        self.session.running_changed.connect(self._on_running_changed)

    # ----------------------------------------------------------------- timers

    def _update_position(self):
        from PyQt6.QtCore import QTime
        now_ms = QTime.currentTime().msecsSinceStartOfDay()
        dt = (now_ms - self._last_tick_ms) / 1000.0
        # Handle day rollover
        if dt < 0:
            dt += 86400.0
        self._last_tick_ms = now_ms
        dt = min(dt, 0.1)  # cap at 100ms to avoid big jumps

        s = self.state
        if s.is_chat_open or s.is_jazzing or s.is_dragging or s.is_sleeping or \
                s.is_preparing_jump or s.is_looking_around:
            return

        if s.is_airborne:
            self._update_airborne(dt)
        else:
            self._advance_along_wall(dt * self._current_walk_speed)

        self.move(int(s.pos_x), int(s.pos_y))
        self.update()

    def _update_airborne(self, dt):
        s = self.state
        self._update_parachute_state()
        gravity = PARACHUTE_GRAVITY if s.is_parachute_open else FALL_GRAVITY
        s.velocity_y += gravity * dt
        if s.is_parachute_open:
            s.velocity_y = min(s.velocity_y, PARACHUTE_TERMINAL)
            s.velocity_x *= 0.992

        s.pos_x += s.velocity_x * dt
        s.pos_y += s.velocity_y * dt

        # Landing checks
        if s.pos_y >= s.max_y:
            s.pos_y = s.max_y
            self._land(RockyWall.BOTTOM)
        elif s.pos_y <= s.min_y:
            s.pos_y = s.min_y
            self._land(RockyWall.TOP)

        if s.pos_x <= s.min_x:
            s.pos_x = s.min_x
            self._land(RockyWall.LEFT)
        elif s.pos_x >= s.max_x:
            s.pos_x = s.max_x
            self._land(RockyWall.RIGHT)

        if s.is_airborne:
            s.direction = 1.0 if s.velocity_x >= 0 else -1.0

    def _update_parachute_state(self):
        s = self.state
        if not s.is_airborne or not s.parachute_eligible:
            s.is_parachute_open = False
            return
        falling_fast = s.velocity_y >= PARACHUTE_DEPLOY
        has_room = s.pos_y < s.max_y - ROCKY_H * 1.35
        s.is_parachute_open = falling_fast and has_room

    def _land(self, wall):
        s = self.state
        prev_wall = s.wall
        s.wall = wall
        s.is_airborne = False
        s.is_parachute_open = False
        s.parachute_eligible = False
        s.clockwise = self._clockwise_after_landing(wall)
        s.velocity_x = 0.0
        s.velocity_y = 0.0
        self._randomize_walk_speed()
        self._update_sprite_facing()
        if wall != prev_wall:
            pass  # corner grip pulse (visual only)

    def _clockwise_after_landing(self, wall):
        s = self.state
        if wall == RockyWall.BOTTOM:
            return s.velocity_x >= 0
        if wall == RockyWall.TOP:
            return s.velocity_x < 0
        if wall == RockyWall.LEFT:
            # In Qt: clockwise on left wall = moving DOWN (+y). Landing from below = vx was positive?
            # Use velocity_y: was moving down before landing (vy > 0 when hitting left)
            return s.velocity_y >= 0
        # RIGHT
        return s.velocity_y <= 0

    def _advance_along_wall(self, distance):
        s = self.state
        d = max(0.0, distance)
        prev_wall = s.wall

        if s.wall == RockyWall.BOTTOM:
            if s.clockwise:   # moving right
                s.pos_x += d
                if s.pos_x >= s.max_x:
                    s.pos_x = s.max_x
                    s.wall = RockyWall.RIGHT
            else:             # moving left
                s.pos_x -= d
                if s.pos_x <= s.min_x:
                    s.pos_x = s.min_x
                    s.wall = RockyWall.LEFT

        elif s.wall == RockyWall.RIGHT:
            if s.clockwise:   # moving UP (−y in Qt)
                s.pos_y -= d
                if s.pos_y <= s.min_y:
                    s.pos_y = s.min_y
                    s.wall = RockyWall.TOP
            else:             # moving DOWN (+y)
                s.pos_y += d
                if s.pos_y >= s.max_y:
                    s.pos_y = s.max_y
                    s.wall = RockyWall.BOTTOM

        elif s.wall == RockyWall.TOP:
            if s.clockwise:   # moving LEFT (−x)
                s.pos_x -= d
                if s.pos_x <= s.min_x:
                    s.pos_x = s.min_x
                    s.wall = RockyWall.LEFT
            else:             # moving RIGHT (+x)
                s.pos_x += d
                if s.pos_x >= s.max_x:
                    s.pos_x = s.max_x
                    s.wall = RockyWall.RIGHT

        elif s.wall == RockyWall.LEFT:
            if s.clockwise:   # moving DOWN (+y)
                s.pos_y += d
                if s.pos_y >= s.max_y:
                    s.pos_y = s.max_y
                    s.wall = RockyWall.BOTTOM
            else:             # moving UP (−y)
                s.pos_y -= d
                if s.pos_y <= s.min_y:
                    s.pos_y = s.min_y
                    s.wall = RockyWall.TOP

        if s.wall != prev_wall:
            pass  # corner grip pulse (visual)
        self._update_sprite_facing()

    def _update_frame(self):
        s = self.state
        if s.is_jazzing:
            s.jazz_frame_index = (s.jazz_frame_index + 1) % 3
        elif not s.is_chat_open and not s.is_sleeping:
            s.walk_frame_index = (s.walk_frame_index + 1) % 2
        self.update()

    def _update_cursor(self):
        s = self.state
        if s.is_dragging:
            self._clear_cursor_awareness()
            return

        mouse = QCursor.pos()
        win_rect = QRect(int(s.pos_x), int(s.pos_y), ROCKY_W, ROCKY_H)
        if win_rect.contains(mouse):
            self._clear_cursor_awareness()
            return

        cx = s.pos_x + ROCKY_W / 2.0
        cy = s.pos_y + ROCKY_H / 2.0
        dx = mouse.x() - cx
        dy = mouse.y() - cy
        dist = max(1.0, math.hypot(dx, dy))
        raw_prox = (CURSOR_NOTICE_DIST - dist) / (CURSOR_NOTICE_DIST - CURSOR_FACING_MIN)
        prox = min(1.0, max(0.0, raw_prox))

        if prox <= 0 or dist < CURSOR_FACING_MIN:
            self._clear_cursor_awareness()
            return

        s.cursor_vector_x = dx / dist
        s.cursor_vector_y = dy / dist
        s.cursor_proximity = prox

        from PyQt6.QtCore import QTime
        now_ms = QTime.currentTime().msecsSinceStartOfDay()
        since_facing = (now_ms - self._last_cursor_facing_ms) / 1000.0

        if (not s.is_sleeping and not s.is_airborne and not s.is_preparing_jump
                and not s.is_looking_around and dist >= CURSOR_FACING_MIN
                and prox > 0.22 and since_facing > CURSOR_FACING_COOLDOWN):
            self._last_cursor_facing_ms = now_ms
            self._turn_toward_cursor(dx, dy)

    def _clear_cursor_awareness(self):
        s = self.state
        if s.cursor_proximity != 0:
            s.cursor_proximity = 0.0
            s.cursor_vector_x = 0.0
            s.cursor_vector_y = 0.0

    def _turn_toward_cursor(self, dx, dy):
        s = self.state
        if s.wall == RockyWall.BOTTOM:
            if abs(dx) < CURSOR_FACING_AXIS:
                return
            new_dir = 1.0 if dx >= 0 else -1.0
        elif s.wall == RockyWall.TOP:
            if abs(dx) < CURSOR_FACING_AXIS:
                return
            new_dir = -1.0 if dx >= 0 else 1.0
        elif s.wall == RockyWall.LEFT:
            if abs(dy) < CURSOR_FACING_AXIS:
                return
            # In Qt: dy > 0 = cursor is below Rocky. On left wall, facing "up" = direction -1
            new_dir = -1.0 if dy >= 0 else 1.0
        else:  # RIGHT
            if abs(dy) < CURSOR_FACING_AXIS:
                return
            new_dir = 1.0 if dy >= 0 else -1.0

        if s.direction != new_dir:
            s.direction = new_dir
            self.update()

    # ----------------------------------------------------------------- jazz

    def _on_running_changed(self, running):
        if self._prev_running and not running:
            self._start_jazz(3.0)
            self._send_notification()
            self._show_speech_bubble('rocky done!', expire=2.5)
        elif running:
            self._wake_rocky(show_bubble=False)
            self._show_speech_bubble(random.choice(WORKING_MESSAGES))
        self._prev_running = running

    def _start_jazz(self, duration):
        if self.state.is_jazzing:
            return
        self.state.is_jazzing = True
        QTimer.singleShot(int(duration * 1000), self._stop_jazz)

    def _stop_jazz(self):
        self.state.is_jazzing = False
        self.update()

    def _schedule_random_jazz(self):
        delay = random.uniform(15, 45)
        QTimer.singleShot(int(delay * 1000), self._random_jazz_tick)

    def _random_jazz_tick(self):
        s = self.state
        if not s.is_chat_open and not s.is_airborne and not s.is_sleeping:
            self._start_jazz(2.0)
            self._show_speech_bubble(random.choice(JAZZ_MESSAGES), expire=2.0)
        self._schedule_random_jazz()

    # ----------------------------------------------------------------- jump

    def _schedule_random_jump(self, first=False):
        delay = random.uniform(4, 8) if first else random.uniform(14, 30)
        QTimer.singleShot(int(delay * 1000), self._random_jump_tick)

    def _random_jump_tick(self):
        s = self.state
        if (not s.is_chat_open and not s.is_jazzing and not s.is_dragging
                and not s.is_airborne and not s.is_preparing_jump
                and not s.is_looking_around and not s.is_sleeping):
            self._start_jump()
        self._schedule_random_jump()

    def _start_jump(self):
        s = self.state
        if s.is_preparing_jump or s.is_airborne or s.is_sleeping or s.is_dragging:
            return
        s.is_preparing_jump = True
        QTimer.singleShot(120, self._launch_jump)

    def _launch_jump(self):
        s = self.state
        if (not s.is_preparing_jump or s.is_chat_open or s.is_jazzing
                or s.is_dragging or s.is_airborne or s.is_sleeping):
            s.is_preparing_jump = False
            return

        s.is_preparing_jump = False
        s.is_airborne = True
        high_jump = random.random() < 0.32
        s.parachute_eligible = high_jump
        s.is_parachute_open = False

        tangent = 1.0 if s.clockwise else -1.0
        lift = random.uniform(1.28, 1.48) if high_jump else 1.0
        drift = random.uniform(0.82, 1.05) if high_jump else 1.0

        if s.wall == RockyWall.BOTTOM:
            s.velocity_x = tangent * self._current_walk_speed * 1.2 * drift
            s.velocity_y = -JUMP_SPEED * lift          # upward = −y in Qt
        elif s.wall == RockyWall.TOP:
            s.velocity_x = -tangent * self._current_walk_speed * 1.2 * drift
            s.velocity_y = JUMP_SPEED * 0.55            # downward
            s.parachute_eligible = False
        elif s.wall == RockyWall.LEFT:
            s.velocity_x = JUMP_SPEED * 0.65 * drift
            # macOS: vy = (tangent*speed*1.1 + 120)*lift upward → Qt: negate
            s.velocity_y = -(tangent * self._current_walk_speed * 1.1 + 120.0) * lift
        else:  # RIGHT
            s.velocity_x = -JUMP_SPEED * 0.65 * drift
            # macOS: vy = (-tangent*speed*1.1 + 120)*lift → Qt: negate
            s.velocity_y = -(-tangent * self._current_walk_speed * 1.1 + 120.0) * lift

        self._update_sprite_facing()

    # ----------------------------------------------------------------- decisions

    def _schedule_random_decision(self, first=False):
        delay = random.uniform(2, 5) if first else random.uniform(3, 8)
        QTimer.singleShot(int(delay * 1000), self._make_movement_decision)

    def _make_movement_decision(self):
        s = self.state
        if (not s.is_chat_open and not s.is_jazzing and not s.is_dragging
                and not s.is_airborne and not s.is_preparing_jump
                and not s.is_looking_around and not s.is_sleeping):
            r = random.randrange(100)
            if r < 42:
                self._reverse_walk_direction()
            elif r < 72:
                self._randomize_walk_speed()
            elif r < 88:
                self._start_jump()
        self._schedule_random_decision()

    def _reverse_walk_direction(self):
        self.state.clockwise = not self.state.clockwise
        self._randomize_walk_speed()
        self._update_sprite_facing()

    def _randomize_walk_speed(self):
        self._current_walk_speed = BASE_WALK_SPEED * random.uniform(0.65, 1.45)

    def _schedule_random_look_around(self, first=False):
        delay = random.uniform(5, 9) if first else random.uniform(10, 22)
        QTimer.singleShot(int(delay * 1000), self._perform_look_around)

    def _perform_look_around(self):
        s = self.state
        if (not s.is_chat_open and not s.is_jazzing and not s.is_dragging
                and not s.is_airborne and not s.is_preparing_jump and not s.is_sleeping):
            s.is_looking_around = True
            original = s.direction
            QTimer.singleShot(160, lambda: self._look_around_flip(original))
            QTimer.singleShot(440, lambda: self._look_around_done(original))
        self._schedule_random_look_around()

    def _look_around_flip(self, original):
        if self.state.is_looking_around:
            self.state.direction = -original
            self.update()

    def _look_around_done(self, original):
        s = self.state
        if s.is_looking_around:
            s.direction = original
            s.is_looking_around = False
            self._update_sprite_facing()
            self.update()

    # ----------------------------------------------------------------- sleep

    def toggle_sleep_mode(self):
        if self.state.is_sleeping:
            self._wake_rocky(show_bubble=True)
        else:
            self._put_to_sleep()

    def _put_to_sleep(self):
        s = self.state
        s.is_sleeping = True
        s.is_airborne = False
        s.is_parachute_open = False
        s.parachute_eligible = False
        s.is_jazzing = False
        s.is_preparing_jump = False
        s.is_looking_around = False
        s.velocity_x = 0.0
        s.velocity_y = 0.0
        self._show_speech_bubble('zzz')
        self.update()

    def _wake_rocky(self, show_bubble):
        s = self.state
        if not s.is_sleeping:
            return
        s.is_sleeping = False
        if show_bubble:
            self._show_speech_bubble('awake', expire=1.2)
        else:
            s.speech_bubble = None
        self.update()

    # ----------------------------------------------------------------- bubble

    def _show_speech_bubble(self, text, expire=None):
        self.state.speech_bubble = text
        if self._speech_bubble_timer:
            self._speech_bubble_timer.stop()
            self._speech_bubble_timer = None
        if expire is not None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._clear_speech_bubble)
            t.start(int(expire * 1000))
            self._speech_bubble_timer = t
        self.update()

    def _clear_speech_bubble(self):
        self.state.speech_bubble = None
        self._speech_bubble_timer = None
        self.update()

    # ----------------------------------------------------------------- sprite helpers

    def _update_sprite_facing(self):
        s = self.state
        if s.is_airborne:
            s.direction = 1.0 if s.velocity_x >= 0 else -1.0
            return
        if s.wall == RockyWall.BOTTOM:
            s.direction = 1.0 if s.clockwise else -1.0
        elif s.wall == RockyWall.TOP:
            s.direction = -1.0 if s.clockwise else 1.0
        elif s.wall == RockyWall.LEFT:
            s.direction = 1.0   # always face screen interior
        else:  # RIGHT
            s.direction = -1.0

    def _current_sprite_name(self):
        s = self.state
        if s.is_sleeping:
            return 'stand'
        if s.is_jazzing:
            return f'jazz{s.jazz_frame_index + 1}'
        if s.is_chat_open:
            return 'stand'
        return 'walkleft1' if s.walk_frame_index == 0 else 'walkleft2'

    def _sprite_rotation(self):
        s = self.state
        if s.is_airborne:
            h_tilt = max(-1.0, min(1.0, s.velocity_x / 360.0)) * 12.0
            v_tilt = max(-1.0, min(1.0, s.velocity_y / 520.0)) * 5.0  # inverted sign for Qt
            return h_tilt + v_tilt
        if s.wall == RockyWall.BOTTOM:
            return 0.0
        if s.wall == RockyWall.RIGHT:
            return -90.0
        if s.wall == RockyWall.TOP:
            return 180.0
        return 90.0  # LEFT

    def _sprite_center_in_window(self):
        """Returns (cx, cy) within the window where the sprite center is drawn."""
        s = self.state
        half = SPRITE_SIZE // 2  # 40
        if s.is_airborne:
            return (ROCKY_W // 2, ROCKY_H // 2)
        if s.wall == RockyWall.BOTTOM:
            return (ROCKY_W // 2, ROCKY_H - half)     # bottom-center
        if s.wall == RockyWall.TOP:
            return (ROCKY_W // 2, half)                # top-center
        if s.wall == RockyWall.LEFT:
            return (half, ROCKY_H // 2)                # left-center
        return (ROCKY_W - half, ROCKY_H // 2)          # right-center

    # ----------------------------------------------------------------- paint

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        s = self.state
        sprite_name = self._current_sprite_name()
        pixmap = self._sprites.get(sprite_name)
        rotation = self._sprite_rotation()
        cx, cy = self._sprite_center_in_window()

        # ---- parachute ----
        if s.is_parachute_open:
            self._draw_parachute(painter, cx, cy, s.velocity_x)

        # ---- sprite ----
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(rotation)
        if s.direction > 0:   # face right = flip sprite
            painter.scale(-1, 1)
        if pixmap:
            painter.drawPixmap(-SPRITE_SIZE // 2, -SPRITE_SIZE // 2, pixmap)
        else:
            painter.setBrush(QColor('#3366ff'))
            painter.drawRoundedRect(-30, -30, 60, 60, 10, 10)
        painter.restore()

        # ---- speech bubble ----
        bubble = self._visible_bubble_text()
        if bubble:
            self._draw_speech_bubble(painter, bubble, cx, cy)

        painter.end()

    def _draw_parachute(self, painter, cx, cy, vx):
        # Canopy above the sprite center
        offset_y = -34
        px = cx + (-4 if vx > 0 else 4)
        py = cy + offset_y

        painter.save()
        painter.translate(px, py)
        tilt = -3 if vx > 0 else 3
        painter.rotate(tilt)

        # Canopy fill (gradient approximation with two colors)
        canopy_path = QPainterPath()
        w, h = 86, 42
        canopy_path.moveTo(-w/2 + w*0.08, h*0.72 - h)
        canopy_path.cubicTo(
            -w/2 + w*0.2, -h*0.08 - h,
            w/2 - w*0.2, -h*0.08 - h,
            w/2 - w*0.08, h*0.72 - h,
        )
        canopy_path.quadTo(0, h*0.96 - h, -w/2 + w*0.08, h*0.72 - h)
        canopy_path.closeSubpath()

        painter.setBrush(QColor(240, 44, 56))
        painter.setPen(QPen(QColor(0, 0, 0, 184), 2))
        painter.drawPath(canopy_path)

        # Rigging lines
        rigging_pen = QPen(QColor(255, 255, 255, 210), 1.6)
        rigging_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(rigging_pen)
        canopy_y = -h + h * 0.34
        harness_y = 20
        for ax in [-w/2 + w*0.24, 0, w/2 - w*0.24]:
            painter.drawLine(int(ax), int(canopy_y), 0, harness_y)

        painter.restore()

    def _visible_bubble_text(self):
        s = self.state
        if s.is_sleeping:
            return s.speech_bubble
        if s.is_airborne or s.wall != RockyWall.BOTTOM:
            return None
        return s.speech_bubble

    def _draw_speech_bubble(self, painter, text, cx, cy):
        s = self.state
        font = QFont('monospace', 11 if not s.is_sleeping else 13)
        font.setBold(True)
        painter.setFont(font)
        fm = QFontMetrics(font)

        padding_h, padding_v = 10, 6
        max_text_w = ROCKY_W - padding_h * 2 - 8  # 4px margin each side
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, max_text_w)
        bubble_w = fm.horizontalAdvance(text) + padding_h * 2
        bubble_h = fm.height() + padding_v * 2

        if s.is_sleeping:
            # Capsule style
            bx = max(0, min(ROCKY_W - bubble_w, cx - bubble_w // 2))
            by = cy - SPRITE_SIZE // 2 - bubble_h - 6
            painter.setBrush(QColor('white'))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bx, by, bubble_w, bubble_h, bubble_h // 2, bubble_h // 2)
            painter.setPen(QColor('black'))
            painter.drawText(bx + padding_h, by + padding_v + fm.ascent(), text)
        else:
            # Rounded box with tail
            bx = max(0, min(ROCKY_W - bubble_w, cx - bubble_w // 2))
            by = ROCKY_H - SPRITE_SIZE - bubble_h - 16  # above sprite, bottom wall
            tail_w, tail_h = 14, 8

            painter.setBrush(QColor('white'))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bx, by, bubble_w, bubble_h, 10, 10)

            # Tail (triangle pointing down, below box)
            tail_path = QPainterPath()
            tx = cx
            tail_path.moveTo(tx, by + bubble_h + tail_h)
            tail_path.lineTo(tx - tail_w // 2, by + bubble_h)
            tail_path.lineTo(tx + tail_w // 2, by + bubble_h)
            tail_path.closeSubpath()
            painter.drawPath(tail_path)

            painter.setPen(QColor('black'))
            painter.drawText(bx + padding_h, by + padding_v + fm.ascent(), text)

    # ----------------------------------------------------------------- mouse

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_global = event.globalPosition().toPoint()
            self._drag_start_win_pos = QPoint(int(self.state.pos_x), int(self.state.pos_y))
            self._has_dragged = False

    def mouseMoveEvent(self, event):
        if self._drag_start_global is None:
            return
        curr = event.globalPosition().toPoint()
        dx = curr.x() - self._drag_start_global.x()
        dy = curr.y() - self._drag_start_global.y()

        if not self._has_dragged and (dx*dx + dy*dy) >= self._drag_threshold_sq:
            self._has_dragged = True
            self.state.is_dragging = True

        if self._has_dragged:
            new_x = self._drag_start_win_pos.x() + dx
            new_y = self._drag_start_win_pos.y() + dy
            self._move_rocky(new_x, new_y)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        s = self.state
        was_dragging = self._has_dragged

        s.is_dragging = False
        self._drag_start_global = None
        self._drag_start_win_pos = None
        self._has_dragged = False

        if was_dragging:
            self._finish_drag()
        else:
            self._toggle_chat()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            'background:#111; color:#00cc00; font-family:monospace;'
            'border:1px solid #003300;'
        )
        title = 'Wake Rocky' if self.state.is_sleeping else 'Sleep Rocky'
        action = menu.addAction(title)
        action.triggered.connect(self.toggle_sleep_mode)
        menu.exec(event.globalPos())

    def _move_rocky(self, x, y):
        x = max(self.state.min_x, min(self.state.max_x, float(x)))
        y = max(self.state.min_y, min(self.state.max_y, float(y)))
        self.state.pos_x = x
        self.state.pos_y = y
        self.move(int(x), int(y))

    def _finish_drag(self):
        s = self.state
        s.is_airborne = False
        s.is_parachute_open = False
        s.parachute_eligible = False
        s.is_preparing_jump = False
        s.is_looking_around = False
        s.velocity_x = 0.0
        s.velocity_y = 0.0
        self._snap_to_nearest_wall()
        self.move(int(s.pos_x), int(s.pos_y))
        self.update()

    def _snap_to_nearest_wall(self):
        s = self.state
        x = max(s.min_x, min(s.max_x, s.pos_x))
        y = max(s.min_y, min(s.max_y, s.pos_y))
        distances = [
            (RockyWall.BOTTOM, abs(y - s.max_y)),
            (RockyWall.TOP,    abs(y - s.min_y)),
            (RockyWall.LEFT,   abs(x - s.min_x)),
            (RockyWall.RIGHT,  abs(x - s.max_x)),
        ]
        wall = min(distances, key=lambda t: t[1])[0]
        s.wall = wall
        if wall == RockyWall.BOTTOM:
            s.pos_x = x; s.pos_y = s.max_y
        elif wall == RockyWall.TOP:
            s.pos_x = x; s.pos_y = s.min_y
        elif wall == RockyWall.LEFT:
            s.pos_x = s.min_x; s.pos_y = y
        else:
            s.pos_x = s.max_x; s.pos_y = y
        self._update_sprite_facing()

    # ----------------------------------------------------------------- chat

    def _toggle_chat(self):
        if self._chat_window and self._chat_window.isVisible():
            self._chat_window.hide()
            self.state.is_chat_open = False
        else:
            self._open_chat()

    def _open_chat(self):
        from chat_window import ChatWindow
        if self._chat_window is None:
            self._chat_window = ChatWindow(self.session)
            self._chat_window.closed.connect(self._on_chat_closed)
        self._position_chat_window()
        self._chat_window.show()
        self._chat_window.raise_()
        self._chat_window.activateWindow()
        self.state.is_chat_open = True

    def _position_chat_window(self):
        if self._chat_window is None:
            return
        # Place chat window above Rocky (or below if near top)
        screen = QApplication.primaryScreen()
        sg = screen.availableGeometry()
        cw, ch = 420, 520
        cx = int(self.state.pos_x) + ROCKY_W // 2 - cw // 2
        cy = int(self.state.pos_y) - ch - 10
        if cy < sg.top():
            cy = int(self.state.pos_y) + ROCKY_H + 10
        cx = max(sg.left(), min(sg.right() - cw, cx))
        self._chat_window.move(cx, cy)

    def _on_chat_closed(self):
        self.state.is_chat_open = False

    # ----------------------------------------------------------------- notification

    def _send_notification(self):
        try:
            subprocess.Popen(
                ['notify-send', 'Rocky finished', 'rocky done!'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
