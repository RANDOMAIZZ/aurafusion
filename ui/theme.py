"""Тёмная неоновая тема AuraFusion."""

ACCENT = "#00E5FF"
ACCENT2 = "#B388FF"
BG = "#0E1116"
CARD = "#171C24"
SIDEBAR = "#12151B"
TEXT = "#E8EDF3"
MUTED = "#8B95A5"

QSS = f"""
* {{ font-family: 'Segoe UI', Arial; }}
QWidget {{ color: {TEXT}; }}
QLabel {{ color: {TEXT}; }}
QCheckBox {{ color: {TEXT}; }}
QGroupBox {{ color: {TEXT}; }}
QListWidget, QListWidget::item {{ color: {TEXT}; }}
QWidget#Root {{ background: {BG}; color: {TEXT}; }}
QWidget#Sidebar {{ background: {SIDEBAR}; border-right: 1px solid #232A35; color: {TEXT}; }}
QPushButton#NavBtn {{
    background: transparent; color: {MUTED}; border: none;
    text-align: left; padding: 11px 14px; font-size: 13px; border-radius: 10px;
}}
QPushButton#NavBtn:hover {{ background: #1C222C; color: {TEXT}; }}
QPushButton#NavBtn[active="true"] {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #12333B, stop:1 #1A2130);
    color: {ACCENT}; border: 1px solid #1E4A54;
}}
QPushButton#NavGroup {{
    background: transparent; color: {MUTED}; border: none;
    text-align: left; padding: 10px 6px 2px 6px; font-size: 11px; font-weight: 700;
}}
QPushButton#NavGroup:hover {{ color: {ACCENT}; }}
QWidget#Card {{
    background: {CARD}; border: 1px solid #232B38; border-radius: 14px;
}}
QLabel#H1 {{ font-size: 17px; font-weight: 700; }}
QLabel#Hint {{ color: {MUTED}; font-size: 11px; }}
QLabel#Value {{ color: {ACCENT}; font-size: 12px; font-weight: 600; }}
QGroupBox {{
    background: {CARD}; border: 1px solid #232B38; border-radius: 12px;
    margin-top: 14px; padding-top: 10px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 6px; background: #242C39; border-radius: 3px; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
    background: {ACCENT}; border: 2px solid #062A31;
}}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT2}, stop:1 {ACCENT}); border-radius: 3px; }}
QComboBox, QSpinBox {{
    background: #1B212C; color: {TEXT}; border: 1px solid #2A3342;
    border-radius: 8px; padding: 6px 10px; min-height: 22px;
}}
QComboBox QAbstractItemView {{ background: #1B212C; color: {TEXT}; selection-background-color: #23414B; }}
QPushButton#Primary {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #00C8E0, stop:1 #7C6CFF);
    color: white; border: none; border-radius: 10px; padding: 9px 18px;
    font-size: 13px; font-weight: 700;
}}
QPushButton#Primary:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #19E0F8, stop:1 #9486FF); }}
QPushButton#Ghost {{
    background: #1E2530; color: {TEXT}; border: 1px solid #2C3646;
    border-radius: 10px; padding: 8px 14px;
}}
QPushButton#Ghost:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; border: 1px solid #334052; background: #1B212C; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QLabel#PreviewDot {{ border-radius: 22px; border: 2px solid rgba(255,255,255,40); }}
QProgressBar#Level {{
    background: #1B212C; border: 1px solid #2A3342; border-radius: 6px; text-align: center;
}}
QProgressBar#Level::chunk {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #00E5FF, stop:0.6 #B388FF, stop:1 #FF5C8A);
    border-radius: 5px;
}}
QListWidget {{ background: #141922; border: 1px solid #232B38; border-radius: 10px; padding: 6px; }}
"""
