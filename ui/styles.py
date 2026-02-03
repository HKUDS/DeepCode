"""
DeepCode UI Styles - Cyber/AI Tech Theme
Modernized with Glassmorphism, Neon Accents, and Fluid Typography.
"""


def get_main_styles() -> str:
    return """
    <style>
        /* ------------------- IMPORT FONTS ------------------- */
        @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;700;900&family=JetBrains+Mono:wght@300;400;600&family=Inter:wght@300;400;600;800&display=swap');

        /* ------------------- VARS (CYBER THEME) ------------------- */
        :root {
            /* Colors */
            --bg-dark: #050505;
            --bg-card: rgba(20, 20, 25, 0.6);
            --bg-card-hover: rgba(30, 30, 40, 0.8);

            --primary: #00f2ff;       /* Cyan Neon */
            --secondary: #7000ff;     /* Electric Purple */
            --accent: #ff0055;        /* Cyber Pink */
            --success: #00ff9d;
            --warning: #ffb800;
            --error: #ff2a6d;
            --text-main: #ffffff;
            --text-muted: rgba(255, 255, 255, 0.6);

            /* Glassmorphism */
            --glass-border: 1px solid rgba(255, 255, 255, 0.08);
            --glass-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
            --neon-shadow: 0 0 10px rgba(0, 242, 255, 0.3), 0 0 20px rgba(0, 242, 255, 0.2);

            /* Typography */
            --font-display: 'Orbitron', sans-serif;
            --font-body: 'Inter', sans-serif;
            --font-code: 'JetBrains Mono', monospace;
        }

        /* ------------------- GLOBAL RESET & ANIMATIONS ------------------- */
        .stApp {
            background-color: var(--bg-dark);
            background-image:
                radial-gradient(circle at 15% 50%, rgba(112, 0, 255, 0.08) 0%, transparent 25%),
                radial-gradient(circle at 85% 30%, rgba(0, 242, 255, 0.08) 0%, transparent 25%);
            font-family: var(--font-body);
            color: var(--text-main);
        }

        h1, h2, h3, h4, h5, h6 {
            font-family: var(--font-display) !important;
            letter-spacing: 1px;
        }

        @keyframes pulse-glow {
            0% { box-shadow: 0 0 0 0 rgba(0, 242, 255, 0.4); }
            70% { box-shadow: 0 0 0 10px rgba(0, 242, 255, 0); }
            100% { box-shadow: 0 0 0 0 rgba(0, 242, 255, 0); }
        }

        /* ------------------- CUSTOM COMPONENTS ------------------- */

        /* Header Design */
        .cyber-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 2rem 0;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            margin-bottom: 2rem;
            background: linear-gradient(90deg, rgba(0,0,0,0) 0%, rgba(0, 242, 255, 0.05) 50%, rgba(0,0,0,0) 100%);
        }

        .brand-container {
            display: flex;
            flex-direction: column;
        }

        .brand-title {
            font-family: var(--font-display);
            font-size: 3.5rem;
            font-weight: 900;
            background: linear-gradient(90deg, #fff, var(--primary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -2px;
            text-shadow: 0 0 30px rgba(0, 242, 255, 0.2);
        }

        .brand-subtitle {
            font-family: var(--font-code);
            color: var(--text-muted);
            font-size: 0.9rem;
            letter-spacing: 3px;
            text-transform: uppercase;
            margin-top: 5px;
        }

        .status-indicator {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            padding: 0.6rem 1.2rem;
            background: rgba(0, 255, 157, 0.05);
            border: 1px solid rgba(0, 255, 157, 0.2);
            border-radius: 4px;
            color: var(--success);
            font-family: var(--font-code);
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 10px var(--success);
            animation: pulse-glow 2s infinite;
        }

        /* Feature Cards */
        .feature-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1.5rem;
            margin-bottom: 3rem;
        }

        .cyber-card {
            background: var(--bg-card);
            backdrop-filter: blur(12px);
            border: var(--glass-border);
            padding: 2rem;
            border-radius: 2px; /* More angular for tech feel */
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
            height: 100%;
        }

        .cyber-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 3px;
            height: 0%;
            background: var(--primary);
            transition: height 0.3s ease;
        }

        .cyber-card:hover::before {
            height: 100%;
        }

        .cyber-card:hover {
            transform: translateY(-5px);
            background: var(--bg-card-hover);
            box-shadow: var(--neon-shadow);
            border-color: rgba(0, 242, 255, 0.4);
        }

        .card-icon {
            font-size: 2rem;
            margin-bottom: 1.5rem;
            color: var(--primary);
            filter: drop-shadow(0 0 10px rgba(0, 242, 255, 0.5));
        }

        .card-title {
            font-family: var(--font-display);
            font-weight: 700;
            font-size: 1.2rem;
            margin-bottom: 0.8rem;
            color: white;
        }

        .card-desc {
            font-family: var(--font-body);
            font-size: 0.95rem;
            color: var(--text-muted);
            line-height: 1.6;
        }

        /* ------------------- STREAMLIT OVERRIDES ------------------- */

        /* Sidebar */
        [data-testid="stSidebar"] {
            background-color: #020203;
            border-right: 1px solid rgba(255,255,255,0.05);
        }
        [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
            color: var(--primary) !important;
        }

        /* Inputs (Text, Select, Area) */
        .stTextInput > div > div > input,
        .stSelectbox > div > div > div,
        .stTextArea > div > div > textarea {
            background-color: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.1);
            color: white;
            border-radius: 4px;
            font-family: var(--font-code);
        }

        .stTextInput > div > div > input:focus,
        .stTextArea > div > div > textarea:focus {
            border-color: var(--primary);
            box-shadow: 0 0 15px rgba(0, 242, 255, 0.1);
            background-color: rgba(0,0,0,0.3);
        }

        /* Tabs */
        .stTabs [data-baseweb="tab-list"] {
            gap: 20px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        .stTabs [data-baseweb="tab"] {
            background-color: transparent;
            border-radius: 4px 4px 0 0;
            color: var(--text-muted);
            font-family: var(--font-display);
            padding: 10px 20px;
        }
        .stTabs [aria-selected="true"] {
            background-color: rgba(0, 242, 255, 0.1);
            color: var(--primary);
            border-bottom: 2px solid var(--primary);
        }

        /* Buttons */
        .stButton > button {
            background: transparent;
            border: 1px solid var(--primary);
            border-radius: 4px;
            color: var(--primary);
            font-family: var(--font-display);
            font-weight: 600;
            letter-spacing: 2px;
            transition: all 0.3s;
            text-transform: uppercase;
            padding: 0.5rem 2rem;
            box-shadow: 0 0 10px rgba(0, 242, 255, 0.1);
        }
        .stButton > button:hover {
            background: var(--primary);
            color: #000;
            box-shadow: 0 0 25px rgba(0, 242, 255, 0.6);
            transform: translateY(-2px);
        }

        /* Primary Action Button Override */
        button[kind="primary"] {
            background: linear-gradient(90deg, var(--secondary) 0%, var(--primary) 100%);
            border: none;
            color: white !important;
        }

        /* Expanders */
        .streamlit-expanderHeader {
            background-color: rgba(255,255,255,0.02);
            border-radius: 4px;
            border: 1px solid rgba(255,255,255,0.05);
        }

        /* Code Blocks */
        code {
            font-family: var(--font-code) !important;
            color: var(--primary) !important;
            background-color: rgba(0,0,0,0.3) !important;
        }

        /* Sidebar feed */
        .sidebar-feed-card {
            border: 1px solid rgba(255,255,255,0.08);
            border-left: 3px solid var(--primary);
            padding: 0.75rem;
            border-radius: 4px;
            margin-bottom: 0.75rem;
            background: rgba(255,255,255,0.02);
            box-shadow: 0 4px 12px rgba(0,0,0,0.25);
        }
        .sidebar-feed-card .stage-line {
            display: flex;
            justify-content: space-between;
            font-family: var(--font-display);
            font-size: 0.8rem;
            letter-spacing: 1px;
            margin-bottom: 0.35rem;
        }
        .sidebar-feed-card .stage {
            color: var(--primary);
        }
        .sidebar-feed-card .time {
            color: rgba(255,255,255,0.4);
            font-family: var(--font-code);
        }
        .sidebar-feed-card .message {
            font-size: 0.9rem;
            color: rgba(255,255,255,0.85);
            line-height: 1.4;
        }
        .sidebar-feed-card.level-success {
            border-left-color: var(--success);
        }
        .sidebar-feed-card.level-error {
            border-left-color: var(--error);
        }
        .sidebar-feed-card.level-warning {
            border-left-color: var(--warning);
        }

        .system-monitor-card {
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 6px;
            padding: 1rem;
            background: rgba(0,0,0,0.25);
            margin-bottom: 1.5rem;
            box-shadow: 0 6px 18px rgba(0,0,0,0.35);
        }
        .system-monitor-card .status-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.75rem;
        }
        .system-monitor-card .status-chip {
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 4px;
            padding: 0.5rem 0.75rem;
            font-size: 0.8rem;
            letter-spacing: 1px;
            text-transform: uppercase;
            display: flex;
            justify-content: space-between;
        }
        .system-monitor-card .status-chip span:last-child {
            color: var(--primary);
            font-family: var(--font-display);
        }
        .system-monitor-card .latest-stage {
            margin-top: 1rem;
            font-size: 0.85rem;
            color: rgba(255,255,255,0.7);
        }
        .system-monitor-card .latest-stage strong {
            color: var(--text-primary);
        }

        /* Footer area override */
        footer {visibility: hidden;}

    </style>
    """
