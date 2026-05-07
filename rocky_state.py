import os
import pwd
import random


class RockyWall:
    BOTTOM = 'bottom'
    TOP = 'top'
    LEFT = 'left'
    RIGHT = 'right'


class RockyState:
    def __init__(self):
        self.walk_frame_index = 0
        self.jazz_frame_index = 0
        self.is_jazzing = False
        self.direction = 1.0       # 1=face right, -1=face left
        self.is_chat_open = False
        self.is_dragging = False
        self.is_airborne = False
        self.is_parachute_open = False
        self.is_sleeping = False
        self.is_preparing_jump = False
        self.is_looking_around = False
        self.cursor_proximity = 0.0
        self.cursor_vector_x = 0.0
        self.cursor_vector_y = 0.0
        self.wall = RockyWall.BOTTOM
        self.pos_x = 0.0
        self.pos_y = 0.0           # Qt coords: top-left of window, y increases downward
        self.velocity_x = 0.0
        self.velocity_y = 0.0      # positive = downward (Qt)
        self.parachute_eligible = False
        self.speech_bubble = None
        self.clockwise = random.choice([True, False])
        # Screen bounds in Qt coords (set at startup)
        self.min_x = 0.0
        self.max_x = 0.0
        self.min_y = 0.0
        self.max_y = 0.0

    @property
    def real_home(self):
        try:
            return pwd.getpwuid(os.getuid()).pw_dir
        except Exception:
            return os.path.expanduser('~')
