"""
Streamlit UI Styles Module

Contains all CSS style definitions for the application
"""


def get_main_styles() -> str:
    """
    Get main CSS styles

    Returns:
        CSS styles string
    """
    return """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;700&family=Inter:wght@300;400;600;700&display=swap');

        :root {
            --primary-bg: #0a0e27;
            --secondary-bg: #1a1f3a;
            --accent-bg: #2d3748;
            --card-bg: rgba(45, 55, 72, 0.9);
            --glass-bg: rgba(255, 255, 255, 0.08);
            --glass-border: rgba(255, 255, 255, 0.12);
            --primary-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --accent-gradient: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
            --neon-blue: #64b5f6;
            --neon-cyan: #4dd0e1;
            --neon-green: #81c784;
            --neon-purple: #ba68c8;
            --text-primary: #ffffff;
            --text-secondary: #e3f2fd;
            --text-muted: #90caf9;
            --border-color: rgba(100, 181, 246, 0.2);
        }

        /* Light theme variables - Using dark theme colors */
        :root {
            --light-primary-bg: #0a0e27;
            --light-secondary-bg: #1a1f3a;
            --light-accent-bg: #2d3748;
            --light-card-bg: rgba(45, 55, 72, 0.9);
            --light-border-soft: rgba(100, 181, 246, 0.2);
            --light-border-medium: rgba(100, 181, 246, 0.4);
            --light-text-primary: #ffffff;
            --light-text-secondary: #e3f2fd;
            --light-text-muted: #90caf9;
            --light-accent-blue: #64b5f6;
            --light-accent-cyan: #4dd0e1;
            --light-accent-green: #81c784;
            --light-accent-purple: #ba68c8;
        }

        /* Global app background and text - Enhanced */
        .stApp {
            background: linear-gradient(135deg, var(--primary-bg) 0%, var(--secondary-bg) 100%);
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
        }

        /* Enhanced main container */
        .main .block-container {
            padding-top: 2rem !important;
            padding-bottom: 2rem !important;
            max-width: 1200px !important;
        }

        /* Improved responsiveness for all screen sizes */
        @media (max-width: 1200px) {
            .main .block-container {
                max-width: 95% !important;
                padding-left: 2rem !important;
                padding-right: 2rem !important;
            }
        }

        @media (max-width: 768px) {
            .main .block-container {
                padding-top: 1rem !important;
                padding-left: 1rem !important;
                padding-right: 1rem !important;
            }
        }

        /* Force high contrast for all text elements */
        .stApp * {
            color: var(--text-primary) !important;
        }

        /* Sidebar redesign - dark tech theme */
        .css-1d391kg {
            background: linear-gradient(180deg, #0d1117 0%, #161b22 50%, #21262d 100%) !important;
            border-right: 2px solid var(--neon-cyan) !important;
            box-shadow: 0 0 20px rgba(77, 208, 225, 0.3) !important;
        }

        /* Light mode sidebar - soft and gentle */
        @media (prefers-color-scheme: light) {
            .css-1d391kg {
                background: linear-gradient(180deg, var(--light-primary-bg) 0%, var(--light-secondary-bg) 50%, var(--light-accent-bg) 100%) !important;
                border-right: 1px solid var(--light-border-soft) !important;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05) !important;
            }
        }

        /* Alternative light theme detection for Streamlit light theme */
        [data-theme="light"] .css-1d391kg,
        .css-1d391kg[data-theme="light"] {
            background: linear-gradient(180deg, var(--light-primary-bg) 0%, var(--light-secondary-bg) 50%, var(--light-accent-bg) 100%) !important;
            border-right: 1px solid var(--light-border-soft) !important;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05) !important;
        }

        .css-1d391kg * {
            color: var(--text-primary) !important;
            font-weight: 500 !important;
        }

        .css-1d391kg h3 {
            color: var(--neon-cyan) !important;
            font-weight: 700 !important;
            font-size: 1.2rem !important;
            text-shadow: 0 0 15px rgba(77, 208, 225, 0.6) !important;
            border-bottom: 1px solid rgba(77, 208, 225, 0.3) !important;
            padding-bottom: 0.5rem !important;
            margin-bottom: 1rem !important;
        }

        /* Light mode text styling */
        @media (prefers-color-scheme: light) {
            .css-1d391kg * {
                color: var(--light-text-primary) !important;
                font-weight: 500 !important;
            }

            .css-1d391kg h3 {
                color: var(--light-accent-blue) !important;
                font-weight: 600 !important;
                font-size: 1.2rem !important;
                text-shadow: none !important;
                border-bottom: 1px solid var(--light-border-soft) !important;
                padding-bottom: 0.5rem !important;
                margin-bottom: 1rem !important;
            }
        }

        /* Alternative light theme detection */
        [data-theme="light"] .css-1d391kg *,
        .css-1d391kg[data-theme="light"] * {
            color: var(--light-text-primary) !important;
            font-weight: 500 !important;
        }

        [data-theme="light"] .css-1d391kg h3,
        .css-1d391kg[data-theme="light"] h3 {
            color: var(--light-accent-blue) !important;
            font-weight: 600 !important;
            font-size: 1.2rem !important;
            text-shadow: none !important;
            border-bottom: 1px solid var(--light-border-soft) !important;
            padding-bottom: 0.5rem !important;
            margin-bottom: 1rem !important;
        }

        .css-1d391kg p, .css-1d391kg div {
            color: var(--text-primary) !important;
            font-weight: 600 !important;
        }

        /* Sidebar info boxes - dark tech style */
        .css-1d391kg .stAlert,
        .css-1d391kg .stInfo,
        .css-1d391kg .stSuccess,
        .css-1d391kg .stWarning,
        .css-1d391kg .stError {
            background: linear-gradient(135deg, #0d1117 0%, #161b22 100%) !important;
            border: 2px solid var(--neon-cyan) !important;
            color: var(--text-primary) !important;
            font-weight: 700 !important;
            border-radius: 12px !important;
            box-shadow: 0 0 15px rgba(77, 208, 225, 0.3) !important;
            backdrop-filter: blur(10px) !important;
            margin: 0.5rem 0 !important;
            padding: 1rem !important;
        }

        /* Light mode info boxes - soft and gentle */
        @media (prefers-color-scheme: light) {
            .css-1d391kg .stAlert,
            .css-1d391kg .stInfo,
            .css-1d391kg .stSuccess,
            .css-1d391kg .stWarning,
            .css-1d391kg .stError {
                background: var(--light-card-bg) !important;
                border: 1px solid var(--light-border-soft) !important;
                color: var(--light-text-primary) !important;
                font-weight: 500 !important;
                border-radius: 8px !important;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1) !important;
                backdrop-filter: none !important;
                margin: 0.5rem 0 !important;
                padding: 0.8rem !important;
            }

            .css-1d391kg .stInfo {
                border-left: 3px solid var(--light-accent-blue) !important;
            }

            .css-1d391kg .stSuccess {
                border-left: 3px solid var(--light-accent-green) !important;
            }

            .css-1d391kg .stWarning {
                border-left: 3px solid #f59e0b !important;
            }

            .css-1d391kg .stError {
                border-left: 3px solid #ef4444 !important;
            }
        }

        /* Alternative light theme detection for info boxes */
        [data-theme="light"] .css-1d391kg .stAlert,
        [data-theme="light"] .css-1d391kg .stInfo,
        [data-theme="light"] .css-1d391kg .stSuccess,
        [data-theme="light"] .css-1d391kg .stWarning,
        [data-theme="light"] .css-1d391kg .stError,
        .css-1d391kg[data-theme="light"] .stAlert,
        .css-1d391kg[data-theme="light"] .stInfo,
        .css-1d391kg[data-theme="light"] .stSuccess,
        .css-1d391kg[data-theme="light"] .stWarning,
        .css-1d391kg[data-theme="light"] .stError {
            background: var(--light-card-bg) !important;
            border: 1px solid var(--light-border-soft) !important;
            color: var(--light-text-primary) !important;
            font-weight: 500 !important;
            border-radius: 8px !important;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1) !important;
            backdrop-filter: none !important;
            margin: 0.5rem 0 !important;
            padding: 0.8rem !important;
        }

        /* Force white text for sidebar info boxes */
        .css-1d391kg .stInfo div,
        .css-1d391kg .stInfo p,
        .css-1d391kg .stInfo span {
            color: #ffffff !important;
            font-weight: 700 !important;
            font-size: 0.9rem !important;
        }

        /* Light mode: Override white text for sidebar info boxes */
        @media (prefers-color-scheme: light) {
            .css-1d391kg .stInfo div,
            .css-1d391kg .stInfo p,
            .css-1d391kg .stInfo span {
                color: var(--light-text-primary) !important;
                font-weight: 600 !important;
                font-size: 0.9rem !important;
            }
        }

        /* Alternative light theme detection for info box text */
        [data-theme="light"] .css-1d391kg .stInfo div,
        [data-theme="light"] .css-1d391kg .stInfo p,
        [data-theme="light"] .css-1d391kg .stInfo span,
        .css-1d391kg[data-theme="light"] .stInfo div,
        .css-1d391kg[data-theme="light"] .stInfo p,
        .css-1d391kg[data-theme="light"] .stInfo span {
            color: var(--light-text-primary) !important;
            font-weight: 600 !important;
            font-size: 0.9rem !important;
        }

        /* Light mode: Override all alert/info box text colors */
        @media (prefers-color-scheme: light) {
            .css-1d391kg .stAlert div,
            .css-1d391kg .stAlert p,
            .css-1d391kg .stAlert span,
            .css-1d391kg .stSuccess div,
            .css-1d391kg .stSuccess p,
            .css-1d391kg .stSuccess span,
            .css-1d391kg .stWarning div,
            .css-1d391kg .stWarning p,
            .css-1d391kg .stWarning span,
            .css-1d391kg .stError div,
            .css-1d391kg .stError p,
            .css-1d391kg .stError span {
                color: var(--light-text-primary) !important;
                font-weight: 600 !important;
                font-size: 0.9rem !important;
            }
        }

        /* Alternative light theme detection for all alert boxes */
        [data-theme="light"] .css-1d391kg .stAlert div,
        [data-theme="light"] .css-1d391kg .stAlert p,
        [data-theme="light"] .css-1d391kg .stAlert span,
        [data-theme="light"] .css-1d391kg .stSuccess div,
        [data-theme="light"] .css-1d391kg .stSuccess p,
        [data-theme="light"] .css-1d391kg .stSuccess span,
        [data-theme="light"] .css-1d391kg .stWarning div,
        [data-theme="light"] .css-1d391kg .stWarning p,
        [data-theme="light"] .css-1d391kg .stWarning span,
        [data-theme="light"] .css-1d391kg .stError div,
        [data-theme="light"] .css-1d391kg .stError p,
        [data-theme="light"] .css-1d391kg .stError span,
        .css-1d391kg[data-theme="light"] .stAlert div,
        .css-1d391kg[data-theme="light"] .stAlert p,
        .css-1d391kg[data-theme="light"] .stAlert span,
        .css-1d391kg[data-theme="light"] .stSuccess div,
        .css-1d391kg[data-theme="light"] .stSuccess p,
        .css-1d391kg[data-theme="light"] .stSuccess span,
        .css-1d391kg[data-theme="light"] .stWarning div,
        .css-1d391kg[data-theme="light"] .stWarning p,
        .css-1d391kg[data-theme="light"] .stWarning span,
        .css-1d391kg[data-theme="light"] .stError div,
        .css-1d391kg[data-theme="light"] .stError p,
        .css-1d391kg[data-theme="light"] .stError span {
            color: var(--light-text-primary) !important;
            font-weight: 600 !important;
            font-size: 0.9rem !important;
        }

        /* ============================================
           LIGHT MODE: COMPREHENSIVE TEXT OVERRIDE
           ============================================ */

        /* Light mode: Comprehensive sidebar text color override */
        @media (prefers-color-scheme: light) {
            .css-1d391kg,
            .css-1d391kg * {
                color: var(--light-text-primary) !important;
            }

            .css-1d391kg h1,
            .css-1d391kg h2,
            .css-1d391kg h3,
            .css-1d391kg h4,
            .css-1d391kg h5,
            .css-1d391kg h6 {
                color: var(--light-accent-blue) !important;
            }
        }

        /* Alternative light theme detection - Comprehensive override */
        [data-theme="light"] .css-1d391kg,
        [data-theme="light"] .css-1d391kg *,
        .css-1d391kg[data-theme="light"],
        .css-1d391kg[data-theme="light"] * {
            color: var(--light-text-primary) !important;
        }

        [data-theme="light"] .css-1d391kg h1,
        [data-theme="light"] .css-1d391kg h2,
        [data-theme="light"] .css-1d391kg h3,
        [data-theme="light"] .css-1d391kg h4,
        [data-theme="light"] .css-1d391kg h5,
        [data-theme="light"] .css-1d391kg h6,
        .css-1d391kg[data-theme="light"] h1,
        .css-1d391kg[data-theme="light"] h2,
        .css-1d391kg[data-theme="light"] h3,
        .css-1d391kg[data-theme="light"] h4,
        .css-1d391kg[data-theme="light"] h5,
        .css-1d391kg[data-theme="light"] h6 {
            color: var(--light-accent-blue) !important;
        }

        /* ================================
           AI ANIMATION EFFECTS & LOGOS
           ================================ */

        /* AI Brain Logo Animation */
        .ai-brain-logo {
            position: absolute;
            width: 80px;
            height: 80px;
            z-index: 1;
        }

        .brain-node {
            position: absolute;
            width: 12px;
            height: 12px;
            background: var(--neon-cyan);
            border-radius: 50%;
            box-shadow: 0 0 15px var(--neon-cyan);
            animation: brainPulse 2s ease-in-out infinite;
        }

        .brain-node.node-1 {
            top: 10px;
            left: 20px;
            animation-delay: 0s;
        }

        .brain-node.node-2 {
            top: 30px;
            right: 15px;
            animation-delay: 0.7s;
        }

        .brain-node.node-3 {
            bottom: 15px;
            left: 30px;
            animation-delay: 1.4s;
        }

        .brain-connection {
            position: absolute;
            background: linear-gradient(45deg, transparent, var(--neon-cyan), transparent);
            height: 2px;
            border-radius: 1px;
            animation: connectionFlow 3s ease-in-out infinite;
        }

        .brain-connection.conn-1 {
            width: 30px;
            top: 20px;
            left: 25px;
            transform: rotate(45deg);
        }

        .brain-connection.conn-2 {
            width: 25px;
            top: 40px;
            left: 15px;
            transform: rotate(-30deg);
            animation-delay: 1s;
        }

        @keyframes brainPulse {
            0%, 100% {
                transform: scale(1);
                opacity: 0.7;
                box-shadow: 0 0 15px var(--neon-cyan);
            }
            50% {
                transform: scale(1.3);
                opacity: 1;
                box-shadow: 0 0 25px var(--neon-cyan), 0 0 35px var(--neon-cyan);
            }
        }

        @keyframes connectionFlow {
            0% {
                opacity: 0;
                background: linear-gradient(45deg, transparent, transparent, transparent);
            }
            50% {
                opacity: 1;
                background: linear-gradient(45deg, transparent, var(--neon-cyan), transparent);
            }
            100% {
                opacity: 0;
                background: linear-gradient(45deg, transparent, transparent, transparent);
            }
        }

        /* Multi-Agent Logo Animation */
        .multi-agent-logo {
            position: absolute;
            width: 80px;
            height: 80px;
            z-index: 1;
        }

        .agent-node {
            position: absolute;
            width: 20px;
            height: 20px;
            background: rgba(186, 104, 200, 0.2);
            border: 2px solid var(--neon-purple);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            animation: agentOrbit 4s linear infinite;
        }

        .agent-node.agent-1 {
            top: 5px;
            left: 30px;
            animation-delay: 0s;
        }

        .agent-node.agent-2 {
            top: 30px;
            right: 5px;
            animation-delay: 1.3s;
        }

        .agent-node.agent-3 {
            bottom: 5px;
            left: 15px;
            animation-delay: 2.6s;
        }

        .agent-connection {
            position: absolute;
            background: linear-gradient(90deg, transparent, var(--neon-purple), transparent);
            height: 1px;
            animation: agentSync 3s ease-in-out infinite;
        }

        .agent-connection.conn-12 {
            width: 25px;
            top: 20px;
            left: 40px;
            transform: rotate(30deg);
        }

        .agent-connection.conn-23 {
            width: 30px;
            top: 45px;
            left: 25px;
            transform: rotate(-45deg);
            animation-delay: 1s;
        }

        .agent-connection.conn-13 {
            width: 20px;
            top: 25px;
            left: 25px;
            transform: rotate(90deg);
            animation-delay: 2s;
        }

        @keyframes agentOrbit {
            0% { transform: rotate(0deg) translateX(15px) rotate(0deg); }
            100% { transform: rotate(360deg) translateX(15px) rotate(-360deg); }
        }

        @keyframes agentSync {
            0%, 100% { opacity: 0.3; }
            50% { opacity: 1; }
        }

        /* Future Vision Orbit Logo */
        .future-logo {
            position: absolute;
            width: 80px;
            height: 80px;
            z-index: 1;
        }

        .orbit {
            position: absolute;
            border: 1px solid rgba(129, 199, 132, 0.3);
            border-radius: 50%;
            animation: orbitRotation 8s linear infinite;
        }

        .orbit-1 {
            width: 60px;
            height: 60px;
            top: 10px;
            left: 10px;
        }

        .orbit-2 {
            width: 40px;
            height: 40px;
            top: 20px;
            left: 20px;
            animation-direction: reverse;
            animation-duration: 6s;
        }

        .orbit-node {
            position: absolute;
            top: -8px;
            left: 50%;
            transform: translateX(-50%);
            width: 16px;
            height: 16px;
            background: var(--neon-green);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 8px;
            box-shadow: 0 0 10px var(--neon-green);
        }

        .orbit-center {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 20px;
            height: 20px;
            background: radial-gradient(circle, var(--neon-green), var(--neon-cyan));
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            animation: centerPulse 2s ease-in-out infinite;
        }

        @keyframes orbitRotation {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        @keyframes centerPulse {
            0%, 100% {
                transform: translate(-50%, -50%) scale(1);
                box-shadow: 0 0 15px var(--neon-green);
            }
            50% {
                transform: translate(-50%, -50%) scale(1.2);
                box-shadow: 0 0 25px var(--neon-green), 0 0 35px var(--neon-cyan);
            }
        }

        /* Open Source Logo Animation */
        .opensource-logo {
            position: absolute;
            width: 80px;
            height: 80px;
            z-index: 1;
        }

        .github-stars {
            position: absolute;
            width: 100%;
            height: 100%;
        }

        .star {
            position: absolute;
            font-size: 14px;
            animation: starTwinkle 3s ease-in-out infinite;
        }

        .star-1 {
            top: 10px;
            left: 15px;
            animation-delay: 0s;
        }

        .star-2 {
            top: 15px;
            right: 10px;
            animation-delay: 1s;
        }

        .star-3 {
            bottom: 10px;
            left: 25px;
            animation-delay: 2s;
        }

        .community-nodes {
            position: absolute;
            width: 100%;
            height: 100%;
        }

        .community-node {
            position: absolute;
            width: 18px;
            height: 18px;
            background: rgba(100, 181, 246, 0.2);
            border: 1px solid var(--neon-blue);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            animation: communityFloat 4s ease-in-out infinite;
        }

        .community-node:nth-child(1) {
            top: 35px;
            left: 5px;
            animation-delay: 0s;
        }

        .community-node:nth-child(2) {
            top: 25px;
            right: 8px;
            animation-delay: 1.3s;
        }

        .community-node:nth-child(3) {
            bottom: 20px;
            left: 30px;
            animation-delay: 2.6s;
        }

        @keyframes starTwinkle {
            0%, 100% {
                opacity: 0.5;
                transform: scale(1);
            }
            50% {
                opacity: 1;
                transform: scale(1.3);
            }
        }

        @keyframes communityFloat {
            0%, 100% {
                transform: translateY(0);
                opacity: 0.7;
            }
            50% {
                transform: translateY(-5px);
                opacity: 1;
            }
        }

        /* Typing number animation */
        .typing-number {
            animation: numberCount 2s ease-out;
        }

        @keyframes numberCount {
            0% {
                opacity: 0;
                transform: scale(0.5);
            }
            100% {
                opacity: 1;
                transform: scale(1);
            }
        }

        /* Sidebar buttons - tech style */
        .css-1d391kg .stButton button {
            background: linear-gradient(135deg, var(--neon-cyan) 0%, var(--neon-blue) 100%) !important;
            color: #000000 !important;
            font-weight: 800 !important;
            border: 2px solid var(--neon-cyan) !important;
            border-radius: 10px !important;
            box-shadow: 0 0 20px rgba(77, 208, 225, 0.4) !important;
            text-shadow: none !important;
            transition: all 0.3s ease !important;
        }

        .css-1d391kg .stButton button:hover {
            box-shadow: 0 0 30px rgba(77, 208, 225, 0.6) !important;
            transform: translateY(-2px) !important;
        }

        /* Light mode sidebar buttons - gentle and modern */
        @media (prefers-color-scheme: light) {
            .css-1d391kg .stButton button {
                background: linear-gradient(135deg, var(--light-accent-blue) 0%, var(--light-accent-cyan) 100%) !important;
                color: #ffffff !important;
                font-weight: 600 !important;
                border: 1px solid var(--light-accent-blue) !important;
                border-radius: 6px !important;
                box-shadow: 0 2px 4px rgba(59, 130, 246, 0.15) !important;
                text-shadow: none !important;
                transition: all 0.2s ease !important;
            }

            .css-1d391kg .stButton button:hover {
                box-shadow: 0 4px 8px rgba(59, 130, 246, 0.25) !important;
                transform: translateY(-1px) !important;
            }
        }

        /* Alternative light theme detection for buttons */
        [data-theme="light"] .css-1d391kg .stButton button,
        .css-1d391kg[data-theme="light"] .stButton button {
            background: linear-gradient(135deg, var(--light-accent-blue) 0%, var(--light-accent-cyan) 100%) !important;
            color: #ffffff !important;
            font-weight: 600 !important;
            border: 1px solid var(--light-accent-blue) !important;
            border-radius: 6px !important;
            box-shadow: 0 2px 4px rgba(59, 130, 246, 0.15) !important;
            text-shadow: none !important;
            transition: all 0.2s ease !important;
        }

        [data-theme="light"] .css-1d391kg .stButton button:hover,
        .css-1d391kg[data-theme="light"] .stButton button:hover {
            box-shadow: 0 4px 8px rgba(59, 130, 246, 0.25) !important;
            transform: translateY(-1px) !important;
        }

        /* Sidebar expanders - dark tech theme */
        .css-1d391kg .streamlit-expanderHeader {
            background: linear-gradient(135deg, #0d1117 0%, #161b22 100%) !important;
            color: var(--text-primary) !important;
            border: 2px solid var(--neon-purple) !important;
            font-weight: 700 !important;
            border-radius: 10px !important;
            box-shadow: 0 0 10px rgba(186, 104, 200, 0.3) !important;
        }

        .css-1d391kg .streamlit-expanderContent {
            background: linear-gradient(135deg, #0d1117 0%, #161b22 100%) !important;
            border: 2px solid var(--neon-purple) !important;
            color: var(--text-primary) !important;
            border-radius: 0 0 10px 10px !important;
            box-shadow: 0 0 10px rgba(186, 104, 200, 0.2) !important;
        }

        /* Light mode sidebar expanders - clean and minimal */
        @media (prefers-color-scheme: light) {
            .css-1d391kg .streamlit-expanderHeader {
                background: var(--light-card-bg) !important;
                color: var(--light-text-primary) !important;
                border: 1px solid var(--light-border-medium) !important;
                font-weight: 600 !important;
                border-radius: 6px !important;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08) !important;
            }

            .css-1d391kg .streamlit-expanderContent {
                background: var(--light-card-bg) !important;
                border: 1px solid var(--light-border-medium) !important;
                color: var(--light-text-primary) !important;
                border-radius: 0 0 6px 6px !important;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05) !important;
                border-top: none !important;
            }
        }

        /* Alternative light theme detection for expanders */
        [data-theme="light"] .css-1d391kg .streamlit-expanderHeader,
        .css-1d391kg[data-theme="light"] .streamlit-expanderHeader {
            background: var(--light-card-bg) !important;
            color: var(--light-text-primary) !important;
            border: 1px solid var(--light-border-medium) !important;
            font-weight: 600 !important;
            border-radius: 6px !important;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08) !important;
        }

        [data-theme="light"] .css-1d391kg .streamlit-expanderContent,
        .css-1d391kg[data-theme="light"] .streamlit-expanderContent {
            background: var(--light-card-bg) !important;
            border: 1px solid var(--light-border-medium) !important;
            color: var(--light-text-primary) !important;
            border-radius: 0 0 6px 6px !important;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05) !important;
            border-top: none !important;
        }

        /* Force high contrast for all sidebar text elements */
        .css-1d391kg span,
        .css-1d391kg p,
        .css-1d391kg div,
        .css-1d391kg label,
        .css-1d391kg strong,
        .css-1d391kg b {
            color: #ffffff !important;
            font-weight: 600 !important;
        }

        /* Light mode: Override all sidebar text colors */
        @media (prefers-color-scheme: light) {
            .css-1d391kg span,
            .css-1d391kg p,
            .css-1d391kg div,
            .css-1d391kg label,
            .css-1d391kg strong,
            .css-1d391kg b {
                color: var(--light-text-primary) !important;
                font-weight: 600 !important;
            }
        }

        /* Alternative light theme detection for all sidebar text */
        [data-theme="light"] .css-1d391kg span,
        [data-theme="light"] .css-1d391kg p,
        [data-theme="light"] .css-1d391kg div,
        [data-theme="light"] .css-1d391kg label,
        [data-theme="light"] .css-1d391kg strong,
        [data-theme="light"] .css-1d391kg b,
        .css-1d391kg[data-theme="light"] span,
        .css-1d391kg[data-theme="light"] p,
        .css-1d391kg[data-theme="light"] div,
        .css-1d391kg[data-theme="light"] label,
        .css-1d391kg[data-theme="light"] strong,
        .css-1d391kg[data-theme="light"] b {
            color: var(--light-text-primary) !important;
            font-weight: 600 !important;
        }

        /* Sidebar markdown content */
        .css-1d391kg [data-testid="stMarkdownContainer"] p {
            color: #ffffff !important;
            font-weight: 600 !important;
            background: none !important;
        }

        /* Light mode: Override sidebar markdown text */
        @media (prefers-color-scheme: light) {
            .css-1d391kg [data-testid="stMarkdownContainer"] p {
                color: var(--light-text-primary) !important;
                font-weight: 600 !important;
                background: none !important;
            }
        }

        /* Alternative light theme detection for markdown content */
        [data-theme="light"] .css-1d391kg [data-testid="stMarkdownContainer"] p,
        .css-1d391kg[data-theme="light"] [data-testid="stMarkdownContainer"] p {
            color: var(--light-text-primary) !important;
            font-weight: 600 !important;
            background: none !important;
        }

        /* Sidebar special styles - system info boxes */
        .css-1d391kg .element-container {
            background: none !important;
        }

        .css-1d391kg .element-container div {
            background: linear-gradient(135deg, #0d1117 0%, #161b22 100%) !important;
            border: 1px solid var(--neon-cyan) !important;
            border-radius: 8px !important;
            padding: 0.8rem !important;
            box-shadow: 0 0 10px rgba(77, 208, 225, 0.2) !important;
            margin: 0.3rem 0 !important;
        }

        /* Processing History special handling */
        .css-1d391kg .stExpander {
            background: linear-gradient(135deg, #0d1117 0%, #161b22 100%) !important;
            border: 2px solid var(--neon-green) !important;
            border-radius: 12px !important;
            box-shadow: 0 0 15px rgba(129, 199, 132, 0.3) !important;
            margin: 0.5rem 0 !important;
        }

        /* Ensure all text is visible on dark background */
        .css-1d391kg .stExpander div,
        .css-1d391kg .stExpander p,
        .css-1d391kg .stExpander span {
            color: #ffffff !important;
            font-weight: 600 !important;
            background: none !important;
        }

        /* Light mode: Override expander text colors */
        @media (prefers-color-scheme: light) {
            .css-1d391kg .stExpander div,
            .css-1d391kg .stExpander p,
            .css-1d391kg .stExpander span {
                color: var(--light-text-primary) !important;
                font-weight: 600 !important;
                background: none !important;
            }
        }

        /* Alternative light theme detection for expander text */
        [data-theme="light"] .css-1d391kg .stExpander div,
        [data-theme="light"] .css-1d391kg .stExpander p,
        [data-theme="light"] .css-1d391kg .stExpander span,
        .css-1d391kg[data-theme="light"] .stExpander div,
        .css-1d391kg[data-theme="light"] .stExpander p,
        .css-1d391kg[data-theme="light"] .stExpander span {
            color: var(--light-text-primary) !important;
            font-weight: 600 !important;
            background: none !important;
        }

        /* Main header area - enhanced version */
        .main-header {
            position: relative;
            background: linear-gradient(135deg,
                rgba(100, 181, 246, 0.12) 0%,
                rgba(77, 208, 225, 0.10) 30%,
                rgba(186, 104, 200, 0.12) 70%,
                rgba(129, 199, 132, 0.10) 100%);
            backdrop-filter: blur(25px);
            border: 1px solid transparent;
            background-clip: padding-box;
            padding: 4rem 2rem;
            border-radius: 25px;
            margin-bottom: 3rem;
            text-align: center;
            overflow: hidden;
            box-shadow:
                0 20px 60px rgba(0, 0, 0, 0.4),
                0 8px 32px rgba(100, 181, 246, 0.2),
                inset 0 1px 0 rgba(255, 255, 255, 0.1);
        }

        .main-header::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(45deg,
                var(--neon-cyan) 0%,
                var(--neon-purple) 25%,
                var(--neon-blue) 50%,
                var(--neon-green) 75%,
                var(--neon-cyan) 100%);
            background-size: 400% 400%;
            border-radius: 25px;
            padding: 1px;
            margin: -1px;
            z-index: -1;
            animation: borderGlow 6s ease-in-out infinite;
        }

        .main-header::after {
            content: '';
            position: absolute;
            top: 50%;
            left: 50%;
            width: 300%;
            height: 300%;
            background: radial-gradient(circle, transparent 30%, rgba(77, 208, 225, 0.03) 60%, transparent 70%);
            transform: translate(-50%, -50%);
            animation: headerPulse 8s ease-in-out infinite;
            pointer-events: none;
        }

        @keyframes headerPulse {
            0%, 100% {
                opacity: 0.3;
                transform: translate(-50%, -50%) scale(1);
            }
            50% {
                opacity: 0.7;
                transform: translate(-50%, -50%) scale(1.1);
            }
        }

        .main-header h1 {
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 3.8rem !important;
            font-weight: 800 !important;
            background: linear-gradient(135deg, var(--neon-blue) 0%, var(--neon-cyan) 40%, #90caf9 80%, var(--neon-blue) 100%) !important;
            background-size: 200% 200% !important;
            -webkit-background-clip: text !important;
            -webkit-text-fill-color: transparent !important;
            background-clip: text !important;
            text-shadow: 0 0 25px rgba(100, 181, 246, 0.4) !important;
            margin-bottom: 1.2rem !important;
            animation: titleGlow 4s ease-in-out infinite alternate, gradientShift 3s ease-in-out infinite !important;
            position: relative;
            z-index: 2;
        }

        @keyframes titleGlow {
            0% {
                filter: drop-shadow(0 0 10px rgba(100, 181, 246, 0.3)) drop-shadow(0 0 15px rgba(100, 181, 246, 0.2));
                text-shadow: 0 0 25px rgba(100, 181, 246, 0.4);
            }
            33% {
                filter: drop-shadow(0 0 12px rgba(77, 208, 225, 0.4)) drop-shadow(0 0 18px rgba(77, 208, 225, 0.25));
                text-shadow: 0 0 30px rgba(77, 208, 225, 0.5);
            }
            66% {
                filter: drop-shadow(0 0 14px rgba(144, 202, 249, 0.35)) drop-shadow(0 0 20px rgba(144, 202, 249, 0.2));
                text-shadow: 0 0 35px rgba(144, 202, 249, 0.45);
            }
            100% {
                filter: drop-shadow(0 0 10px rgba(100, 181, 246, 0.3)) drop-shadow(0 0 15px rgba(100, 181, 246, 0.2));
                text-shadow: 0 0 25px rgba(100, 181, 246, 0.4);
            }
        }

        @keyframes gradientShift {
            0%, 100% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
        }

        .main-header h3 {
            font-family: 'Inter', sans-serif !important;
            font-size: 1.2rem !important;
            font-weight: 400 !important;
            color: var(--text-secondary) !important;
            letter-spacing: 2px !important;
            margin-bottom: 0.5rem !important;
        }

        .main-header p {
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 0.9rem !important;
            color: var(--neon-green) !important;
            letter-spacing: 1px !important;
            font-weight: 600 !important;
        }

        /* Modern Header Styles - Compact & Professional */
        .modern-header {
            background: linear-gradient(135deg, rgba(26, 31, 58, 0.8) 0%, rgba(45, 55, 72, 0.6) 100%);
            border-radius: 16px;
            margin: 1rem 0;
            padding: 1.5rem 2rem;
            backdrop-filter: blur(20px);
            border: 1px solid rgba(100, 181, 246, 0.1);
            position: relative;
            overflow: hidden;
        }

        .modern-header::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg,
                var(--neon-cyan) 0%,
                var(--neon-blue) 50%,
                var(--neon-green) 100%);
            z-index: 1;
        }

        .header-content {
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: relative;
            z-index: 2;
        }

        .logo-section {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .logo-animation {
            display: flex;
            align-items: center;
            gap: 0.8rem;
        }

        .dna-helix {
            position: relative;
            width: 30px;
            height: 30px;
        }

        .helix-strand {
            position: absolute;
            width: 100%;
            height: 2px;
            background: var(--neon-cyan);
            border-radius: 2px;
            animation: helix-rotate 3s infinite linear;
        }

        .helix-strand.strand-1 {
            top: 40%;
            animation-delay: 0s;
        }

        .helix-strand.strand-2 {
            top: 60%;
            animation-delay: 1.5s;
            background: var(--neon-blue);
        }

        @keyframes helix-rotate {
            0% { transform: rotateY(0deg) scaleX(1); }
            25% { transform: rotateY(90deg) scaleX(0.3); }
            50% { transform: rotateY(180deg) scaleX(1); }
            75% { transform: rotateY(270deg) scaleX(0.3); }
            100% { transform: rotateY(360deg) scaleX(1); }
        }

        .logo-text {
            font-size: 1.8rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--neon-cyan), var(--neon-blue));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            filter: drop-shadow(0 0 20px rgba(77, 208, 225, 0.3));
        }

        .tagline {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            font-size: 0.9rem;
        }

        .tagline .highlight {
            color: var(--neon-cyan);
            font-weight: 600;
        }

        .tagline .separator {
            color: var(--text-muted);
            opacity: 0.6;
        }

        .tagline .org {
            color: var(--text-secondary);
            font-weight: 400;
        }

        .status-badge {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            background: rgba(129, 199, 132, 0.1);
            border: 1px solid rgba(129, 199, 132, 0.3);
            border-radius: 20px;
            padding: 0.5rem 1rem;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background: var(--neon-green);
            border-radius: 50%;
            animation: status-pulse 2s infinite ease-in-out;
        }

        @keyframes status-pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.6; transform: scale(0.8); }
        }

        .status-text {
            font-size: 0.8rem;
            color: var(--neon-green);
            font-weight: 600;
            letter-spacing: 0.5px;
        }

        /* Responsive modern header */
        @media (max-width: 768px) {
            .modern-header .header-content {
                flex-direction: column;
                gap: 1rem;
                text-align: center;
            }

            .modern-header .tagline {
                flex-wrap: wrap;
                justify-content: center;
            }

            .modern-header .logo-text {
                font-size: 1.5rem;
            }

            .modern-header {
                padding: 1rem 1.5rem;
            }
        }

        /* Streamlit component style overrides */
        .stMarkdown h3 {
            color: var(--neon-cyan) !important;
            font-family: 'Inter', sans-serif !important;
            font-weight: 700 !important;
            font-size: 1.5rem !important;
            text-shadow: 0 0 10px rgba(77, 208, 225, 0.3) !important;
        }

        /* Radio button styles */
        .stRadio > div {
            background: var(--card-bg) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 12px !important;
            padding: 1rem !important;
            backdrop-filter: blur(10px) !important;
        }

        .stRadio label {
            color: var(--text-primary) !important;
            font-weight: 600 !important;
            font-size: 1rem !important;
        }

        .stRadio > div > div > div > label {
            color: var(--text-secondary) !important;
            font-weight: 500 !important;
            font-size: 1rem !important;
        }

        /* File uploader */
        .stFileUploader > div {
            background: var(--card-bg) !important;
            border: 2px dashed var(--border-color) !important;
            border-radius: 15px !important;
            transition: all 0.3s ease !important;
            backdrop-filter: blur(10px) !important;
        }

        .stFileUploader > div:hover {
            border-color: var(--neon-cyan) !important;
            box-shadow: 0 0 20px rgba(77, 208, 225, 0.3) !important;
        }

        .stFileUploader label {
            color: var(--text-primary) !important;
            font-weight: 600 !important;
        }

        .stFileUploader span {
            color: var(--text-secondary) !important;
            font-weight: 500 !important;
        }

        /* Text input fields */
        .stTextInput > div > div > input {
            background: var(--card-bg) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 10px !important;
            color: var(--text-primary) !important;
            font-weight: 500 !important;
            backdrop-filter: blur(10px) !important;
        }

        .stTextInput > div > div > input:focus {
            border-color: var(--neon-cyan) !important;
            box-shadow: 0 0 0 1px var(--neon-cyan) !important;
        }

        .stTextInput label {
            color: var(--text-primary) !important;
            font-weight: 600 !important;
        }

        /* Button styles */
        .stButton > button {
            width: 100% !important;
            background: var(--primary-gradient) !important;
            color: white !important;
            border: none !important;
            border-radius: 12px !important;
            padding: 0.8rem 2rem !important;
            font-family: 'Inter', sans-serif !important;
            font-weight: 600 !important;
            font-size: 1rem !important;
            letter-spacing: 0.5px !important;
            transition: all 0.3s ease !important;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3) !important;
        }

        .stButton > button:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4) !important;
        }

        /* Status message styles */
        .status-success, .stSuccess {
            background: linear-gradient(135deg, rgba(129, 199, 132, 0.15) 0%, rgba(129, 199, 132, 0.05) 100%) !important;
            color: var(--neon-green) !important;
            padding: 1rem 1.5rem !important;
            border-radius: 10px !important;
            border: 1px solid rgba(129, 199, 132, 0.3) !important;
            backdrop-filter: blur(10px) !important;
            font-weight: 600 !important;
        }

        .status-error, .stError {
            background: linear-gradient(135deg, rgba(244, 67, 54, 0.15) 0%, rgba(244, 67, 54, 0.05) 100%) !important;
            color: #ff8a80 !important;
            padding: 1rem 1.5rem !important;
            border-radius: 10px !important;
            border: 1px solid rgba(244, 67, 54, 0.3) !important;
            backdrop-filter: blur(10px) !important;
            font-weight: 600 !important;
        }

        .status-warning, .stWarning {
            background: linear-gradient(135deg, rgba(255, 193, 7, 0.15) 0%, rgba(255, 193, 7, 0.05) 100%) !important;
            color: #ffcc02 !important;
            padding: 1rem 1.5rem !important;
            border-radius: 10px !important;
            border: 1px solid rgba(255, 193, 7, 0.3) !important;
            backdrop-filter: blur(10px) !important;
            font-weight: 600 !important;
        }

        .status-info, .stInfo {
            background: linear-gradient(135deg, rgba(77, 208, 225, 0.15) 0%, rgba(77, 208, 225, 0.05) 100%) !important;
            color: var(--neon-cyan) !important;
            padding: 1rem 1.5rem !important;
            border-radius: 10px !important;
            border: 1px solid rgba(77, 208, 225, 0.3) !important;
            backdrop-filter: blur(10px) !important;
            font-weight: 600 !important;
        }

        /* Progress bar */
        .progress-container {
            margin: 1.5rem 0;
            padding: 2rem;
            background: var(--card-bg);
            backdrop-filter: blur(15px);
            border: 1px solid var(--border-color);
            border-radius: 15px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        }

        .stProgress > div > div > div {
            background: var(--accent-gradient) !important;
            border-radius: 10px !important;
        }

        /* Text area */
        .stTextArea > div > div > textarea {
            background: var(--card-bg) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 10px !important;
            color: var(--text-primary) !important;
            font-family: 'JetBrains Mono', monospace !important;
            backdrop-filter: blur(10px) !important;
        }

        /* Expander */
        .streamlit-expanderHeader {
            background: var(--card-bg) !important;
            color: var(--text-primary) !important;
            border: 1px solid var(--border-color) !important;
            font-weight: 600 !important;
        }

        .streamlit-expanderContent {
            background: var(--card-bg) !important;
            border: 1px solid var(--border-color) !important;
        }

        /* Ensure all Markdown content is visible */
        [data-testid="stMarkdownContainer"] p {
            color: var(--text-secondary) !important;
            font-weight: 500 !important;
        }

        /* Dividers */
        hr {
            border-color: var(--border-color) !important;
            opacity: 0.5 !important;
        }

        /* Scrollbars */
        ::-webkit-scrollbar {
            width: 8px;
        }

        ::-webkit-scrollbar-track {
            background: var(--accent-bg);
            border-radius: 10px;
        }

        ::-webkit-scrollbar-thumb {
            background: var(--accent-gradient);
            border-radius: 10px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: var(--primary-gradient);
        }

        /* Placeholder text */
        ::placeholder {
            color: var(--text-muted) !important;
            opacity: 0.7 !important;
        }

        /* ================================
           AI AGENT CAPABILITIES DISPLAY
           ================================ */

        /* AI Capabilities section - simplified to avoid conflicts with main header */
        .ai-capabilities-section {
            position: relative;
            background: linear-gradient(135deg,
                rgba(77, 208, 225, 0.08) 0%,
                rgba(186, 104, 200, 0.06) 50%,
                rgba(129, 199, 132, 0.08) 100%);
            backdrop-filter: blur(15px);
            border: 1px solid rgba(77, 208, 225, 0.2);
            padding: 2rem 1.5rem;
            border-radius: 20px;
            margin: 2rem 0;
            text-align: center;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
        }

        .ai-capabilities-section::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(90deg,
                transparent 0%,
                rgba(77, 208, 225, 0.1) 50%,
                transparent 100%);
            animation: shimmer 3s ease-in-out infinite;
        }

        @keyframes shimmer {
            0% { transform: translateX(-100%); }
            100% { transform: translateX(100%); }
        }

        @keyframes borderGlow {
            0%, 100% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
        }

        /* Neural network animation */
        .neural-network {
            position: absolute;
            top: 1rem;
            right: 2rem;
            display: flex;
            gap: 0.5rem;
        }

        .neuron {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: var(--neon-cyan);
            box-shadow: 0 0 10px var(--neon-cyan);
            animation-duration: 2s;
            animation-iteration-count: infinite;
            animation-timing-function: ease-in-out;
        }

        .pulse-1 { animation-name: neuronPulse; animation-delay: 0s; }
        .pulse-2 { animation-name: neuronPulse; animation-delay: 0.3s; }
        .pulse-3 { animation-name: neuronPulse; animation-delay: 0.6s; }

        @keyframes neuronPulse {
            0%, 100% {
                transform: scale(1);
                opacity: 0.7;
                box-shadow: 0 0 10px var(--neon-cyan);
            }
            50% {
                transform: scale(1.3);
                opacity: 1;
                box-shadow: 0 0 20px var(--neon-cyan), 0 0 30px var(--neon-cyan);
            }
        }

        .capabilities-title {
            font-family: 'Inter', sans-serif !important;
            font-size: 2rem !important;
            font-weight: 700 !important;
            background: linear-gradient(135deg, var(--neon-cyan), var(--neon-purple), var(--neon-green));
            background-clip: text;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 0 20px rgba(77, 208, 225, 0.3);
            margin-bottom: 0.5rem !important;
            letter-spacing: -0.5px;
        }

        .capabilities-subtitle {
            font-family: 'JetBrains Mono', monospace !important;
            color: var(--neon-cyan) !important;
            font-size: 0.9rem !important;
            letter-spacing: 1.5px !important;
            font-weight: 500 !important;
            text-transform: uppercase;
            opacity: 0.8;
        }

        /* Enhanced feature card system - ensure alignment */
        .feature-card {
            position: relative;
            background: var(--card-bg);
            backdrop-filter: blur(20px);
            border: 1px solid var(--border-color);
            padding: 2.5rem;
            border-radius: 20px;
            margin: 1.5rem 0;
            transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            box-shadow: 0 8px 40px rgba(0, 0, 0, 0.3);
            overflow: hidden;
            /* Ensure card alignment */
            min-height: 420px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        /* AI NEXUS FUTURISTIC LAYOUT - WORLD-CLASS DESIGN */
        .ai-nexus-container {
            background: linear-gradient(135deg,
                rgba(0, 0, 0, 0.95) 0%,
                rgba(15, 20, 42, 0.95) 25%,
                rgba(0, 12, 36, 0.95) 50%,
                rgba(10, 5, 30, 0.95) 75%,
                rgba(0, 0, 0, 0.95) 100%);
            border-radius: 24px;
            padding: 3rem 2rem;
            margin: 2rem 0;
            backdrop-filter: blur(20px);
            border: 1px solid rgba(0, 255, 255, 0.2);
            position: relative;
            overflow: hidden;
            box-shadow:
                0 0 50px rgba(0, 255, 255, 0.1),
                inset 0 0 50px rgba(0, 255, 255, 0.05);
        }

        .ai-nexus-container::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background:
                radial-gradient(circle at 20% 30%, rgba(0, 255, 255, 0.1) 0%, transparent 50%),
                radial-gradient(circle at 80% 70%, rgba(255, 0, 255, 0.08) 0%, transparent 50%),
                radial-gradient(circle at 40% 80%, rgba(0, 255, 0, 0.06) 0%, transparent 50%);
            z-index: -1;
            animation: neural-pulse 4s ease-in-out infinite alternate;
        }

        @keyframes neural-pulse {
            0% { opacity: 0.6; transform: scale(1); }
            100% { opacity: 0.9; transform: scale(1.02); }
        }

        .quantum-header {
            text-align: center;
            margin-bottom: 3rem;
            position: relative;
        }

        .neural-matrix {
            position: relative;
            display: inline-block;
            margin-bottom: 2rem;
            width: 120px;
            height: 80px;
        }

        .matrix-node {
            position: absolute;
            width: 8px;
            height: 8px;
            background: rgba(0, 255, 255, 0.8);
            border-radius: 50%;
            box-shadow: 0 0 20px rgba(0, 255, 255, 0.6);
            animation: node-pulse 2s ease-in-out infinite;
        }

        .matrix-node.node-1 {
            top: 20px;
            left: 20px;
            animation-delay: 0s;
        }

        .matrix-node.node-2 {
            top: 20px;
            right: 20px;
            animation-delay: 0.7s;
        }

        .matrix-node.node-3 {
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            animation-delay: 1.4s;
        }

        @keyframes node-pulse {
            0%, 100% {
                opacity: 0.6;
                transform: scale(1);
                box-shadow: 0 0 20px rgba(0, 255, 255, 0.6);
            }
            50% {
                opacity: 1;
                transform: scale(1.5);
                box-shadow: 0 0 30px rgba(0, 255, 255, 1);
            }
        }

        .matrix-connection {
            position: absolute;
            height: 1px;
            background: linear-gradient(90deg,
                transparent 0%,
                rgba(0, 255, 255, 0.6) 50%,
                transparent 100%);
            animation: connection-flow 3s linear infinite;
        }

        .matrix-connection.conn-1 {
            top: 24px;
            left: 28px;
            width: 64px;
        }

        .matrix-connection.conn-2 {
            top: 24px;
            left: 24px;
            width: 72px;
            transform: rotate(35deg);
            transform-origin: left center;
            animation-delay: 1s;
        }

        .matrix-connection.conn-3 {
            top: 24px;
            right: 24px;
            width: 72px;
            transform: rotate(-35deg);
            transform-origin: right center;
            animation-delay: 2s;
        }

        @keyframes connection-flow {
            0% {
                background: linear-gradient(90deg,
                    transparent 0%,
                    transparent 10%,
                    rgba(0, 255, 255, 0.6) 50%,
                    transparent 90%,
                    transparent 100%);
            }
            100% {
                background: linear-gradient(90deg,
                    transparent 0%,
                    rgba(0, 255, 255, 0.6) 10%,
                    transparent 50%,
                    rgba(0, 255, 255, 0.6) 90%,
                    transparent 100%);
            }
        }

        .nexus-title {
            font-family: 'JetBrains Mono', monospace;
            font-size: 2.2rem;
            font-weight: 800;
            background: linear-gradient(135deg,
                #00ffff 0%,
                #ffffff 25%,
                #00ff00 50%,
                #ffffff 75%,
                #ff00ff 100%);
            background-size: 200% 200%;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin: 0;
            letter-spacing: 3px;
            text-shadow: 0 0 30px rgba(0, 255, 255, 0.3);
            animation: nexus-glow 3s ease-in-out infinite alternate;
        }

        @keyframes nexus-glow {
            0% {
                background-position: 0% 50%;
                filter: brightness(1);
            }
            100% {
                background-position: 100% 50%;
                filter: brightness(1.2);
            }
        }

        .nexus-subtitle {
            font-family: 'JetBrains Mono', monospace;
            font-size: 1rem;
            color: rgba(0, 255, 255, 0.8);
            letter-spacing: 2px;
            margin: 1rem 0 0 0;
            text-transform: uppercase;
            opacity: 0.9;
        }

        .capability-matrix {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 2rem;
            margin: 3rem 0;
        }

        .capability-node {
            background: linear-gradient(135deg,
                rgba(0, 0, 0, 0.6) 0%,
                rgba(15, 25, 45, 0.4) 100%);
            border: 1px solid rgba(0, 255, 255, 0.3);
            border-radius: 16px;
            padding: 2rem;
            position: relative;
            overflow: hidden;
            transition: all 0.4s cubic-bezier(0.23, 1, 0.320, 1);
            backdrop-filter: blur(10px);
        }

        .capability-node::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(135deg,
                rgba(0, 255, 255, 0.05) 0%,
                transparent 50%,
                rgba(255, 0, 255, 0.05) 100%);
            opacity: 0;
            transition: opacity 0.4s ease;
            z-index: -1;
        }

        .capability-node:hover {
            transform: translateY(-8px) scale(1.02);
            border-color: rgba(0, 255, 255, 0.6);
            box-shadow:
                0 20px 60px rgba(0, 0, 0, 0.4),
                0 0 30px rgba(0, 255, 255, 0.2);
        }

        .capability-node:hover::before {
            opacity: 1;
        }

        .node-core {
            position: relative;
            text-align: center;
            margin-bottom: 1.5rem;
        }

        .core-pulse {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 60px;
            height: 60px;
            border: 2px solid rgba(0, 255, 255, 0.6);
            border-radius: 50%;
            animation: core-ripple 2s linear infinite;
        }

        @keyframes core-ripple {
            0% {
                transform: translate(-50%, -50%) scale(0.8);
                opacity: 1;
            }
            100% {
                transform: translate(-50%, -50%) scale(2);
                opacity: 0;
            }
        }

        .core-label {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            font-weight: 700;
            color: #00ffff;
            background: rgba(0, 0, 0, 0.8);
            padding: 0.8rem 1.2rem;
            border-radius: 20px;
            border: 1px solid rgba(0, 255, 255, 0.4);
            letter-spacing: 1px;
            position: relative;
            z-index: 2;
            display: inline-block;
            text-shadow: 0 0 10px rgba(0, 255, 255, 0.5);
        }

        .node-description h3 {
            font-size: 1.4rem;
            font-weight: 600;
            color: #ffffff;
            margin: 0 0 0.8rem 0;
            text-align: center;
        }

        .node-description p {
            font-size: 0.95rem;
            color: rgba(255, 255, 255, 0.8);
            margin: 0;
            text-align: center;
            line-height: 1.5;
        }

        .node-metrics {
            text-align: center;
            margin-top: 1.5rem;
        }

        .metric {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.75rem;
            font-weight: 600;
            color: #00ffff;
            background: linear-gradient(135deg,
                rgba(0, 255, 255, 0.2) 0%,
                rgba(0, 255, 255, 0.1) 100%);
            padding: 0.4rem 0.8rem;
            border-radius: 12px;
            border: 1px solid rgba(0, 255, 255, 0.3);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: inline-block;
        }

        /* Node-specific color themes */
        .research-node {
            border-color: rgba(0, 255, 255, 0.3);
        }
        .research-node .core-label,
        .research-node .metric {
            color: #00ffff;
            border-color: rgba(0, 255, 255, 0.4);
        }

        .interface-node {
            border-color: rgba(0, 255, 0, 0.3);
        }
        .interface-node .core-label,
        .interface-node .metric {
            color: #00ff00;
            border-color: rgba(0, 255, 0, 0.4);
        }

        .architecture-node {
            border-color: rgba(255, 255, 0, 0.3);
        }
        .architecture-node .core-label,
        .architecture-node .metric {
            color: #ffff00;
            border-color: rgba(255, 255, 0, 0.4);
        }

        .text2code-node {
            border-color: rgba(0, 255, 0, 0.3);
        }
        .text2code-node .core-label,
        .text2code-node .metric {
            color: #00ff00;
            border-color: rgba(0, 255, 0, 0.4);
        }

        .intelligence-node {
            border-color: rgba(255, 0, 255, 0.3);
        }
        .intelligence-node .core-label,
        .intelligence-node .metric {
            color: #ff00ff;
            border-color: rgba(255, 0, 255, 0.4);
        }

        .processing-pipeline {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 1rem;
            margin: 3rem 0;
            padding: 2rem;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 16px;
            border: 1px solid rgba(0, 255, 255, 0.2);
            flex-wrap: wrap;
        }

        .pipeline-stage {
            text-align: center;
            position: relative;
        }

        .stage-core {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem;
            font-weight: 700;
            color: #ffffff;
            background: linear-gradient(135deg,
                rgba(0, 255, 255, 0.8) 0%,
                rgba(0, 200, 200, 0.8) 100%);
            padding: 1rem 1.5rem;
            border-radius: 50px;
            border: 2px solid rgba(0, 255, 255, 0.6);
            letter-spacing: 1px;
            text-shadow: 0 0 10px rgba(0, 0, 0, 0.8);
            box-shadow:
                0 0 20px rgba(0, 255, 255, 0.3),
                inset 0 0 20px rgba(255, 255, 255, 0.1);
        }

        .stage-description {
            font-size: 0.75rem;
            color: rgba(0, 255, 255, 0.8);
            margin-top: 0.5rem;
            font-weight: 500;
        }

        .pipeline-flow {
            width: 60px;
            height: 2px;
            background: linear-gradient(90deg,
                transparent 0%,
                rgba(0, 255, 255, 0.6) 50%,
                transparent 100%);
            position: relative;
            margin: 0 1rem;
        }

        .flow-particle {
            position: absolute;
            top: -2px;
            left: 0;
            width: 6px;
            height: 6px;
            background: #00ffff;
            border-radius: 50%;
            box-shadow: 0 0 10px rgba(0, 255, 255, 0.8);
            animation: particle-flow 2s linear infinite;
        }

        @keyframes particle-flow {
            0% {
                left: 0;
                opacity: 0;
            }
            10% {
                opacity: 1;
            }
            90% {
                opacity: 1;
            }
            100% {
                left: 100%;
                opacity: 0;
            }
        }

        .system-status {
            text-align: center;
            margin-top: 2rem;
        }

        .status-indicator {
            display: inline-flex;
            align-items: center;
            gap: 0.8rem;
            background: rgba(0, 0, 0, 0.6);
            padding: 1rem 2rem;
            border-radius: 25px;
            border: 1px solid rgba(0, 255, 0, 0.4);
        }

        .status-pulse {
            width: 10px;
            height: 10px;
            background: #00ff00;
            border-radius: 50%;
            animation: status-heartbeat 1.5s ease-in-out infinite;
            box-shadow: 0 0 15px rgba(0, 255, 0, 0.6);
        }

        @keyframes status-heartbeat {
            0%, 100% {
                transform: scale(1);
                opacity: 1;
            }
            50% {
                transform: scale(1.2);
                opacity: 0.8;
            }
        }

        .status-text {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            font-weight: 600;
            color: #00ff00;
            letter-spacing: 1px;
            text-shadow: 0 0 10px rgba(0, 255, 0, 0.3);
        }

        /* Responsive design for AI Nexus */
        @media (max-width: 768px) {
            .ai-nexus-container {
                padding: 2rem 1rem;
            }

            .capability-matrix {
                grid-template-columns: 1fr;
                gap: 1.5rem;
            }

            .processing-pipeline {
                flex-direction: column;
                gap: 1.5rem;
            }

            .pipeline-flow {
                transform: rotate(90deg);
                width: 30px;
            }

            .nexus-title {
                font-size: 1.8rem;
                letter-spacing: 2px;
            }
        }

        /* LEGACY FEATURES COMPACT */
        .features-compact-container {
            background: linear-gradient(135deg, rgba(45, 55, 72, 0.1) 0%, rgba(26, 31, 58, 0.1) 100%);
            border-radius: 20px;
            padding: 2rem;
            margin: 1.5rem 0;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(100, 181, 246, 0.1);
            position: relative;
            overflow: hidden;
        }

        .features-compact-container::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(45deg,
                rgba(100, 181, 246, 0.05) 0%,
                rgba(77, 208, 225, 0.05) 50%,
                rgba(129, 199, 132, 0.05) 100%);
            z-index: -1;
        }

        .features-header {
            text-align: center;
            margin-bottom: 2rem;
            position: relative;
        }

        .neural-pulse {
            position: relative;
            display: inline-block;
            margin-bottom: 1rem;
        }

        .pulse-dot {
            width: 12px;
            height: 12px;
            background: var(--neon-cyan);
            border-radius: 50%;
            position: relative;
            z-index: 2;
            box-shadow: 0 0 20px var(--neon-cyan);
        }

        .pulse-ring {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 40px;
            height: 40px;
            border: 2px solid var(--neon-cyan);
            border-radius: 50%;
            opacity: 0.6;
            animation: pulse-expand 2s infinite ease-out;
        }

        @keyframes pulse-expand {
            0% {
                transform: translate(-50%, -50%) scale(0.5);
                opacity: 0.8;
            }
            100% {
                transform: translate(-50%, -50%) scale(2);
                opacity: 0;
            }
        }

        .platform-title {
            font-size: 2rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--neon-cyan), var(--neon-blue));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin: 0.5rem 0;
            text-shadow: 0 0 30px rgba(77, 208, 225, 0.3);
        }

        .platform-subtitle {
            font-size: 1.1rem;
            color: var(--text-secondary);
            margin: 0;
            opacity: 0.9;
        }

        .features-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1.5rem;
            margin: 2rem 0;
        }

        .feature-pill {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            padding: 1.5rem;
            position: relative;
            overflow: hidden;
            transition: all 0.3s ease;
            backdrop-filter: blur(10px);
            cursor: pointer;
        }

        .feature-pill:hover {
            transform: translateY(-5px);
            box-shadow: 0 15px 40px rgba(0, 0, 0, 0.2);
            border-color: rgba(255, 255, 255, 0.2);
        }

        .pill-glow {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(135deg,
                rgba(100, 181, 246, 0.1) 0%,
                rgba(77, 208, 225, 0.05) 100%);
            opacity: 0;
            transition: opacity 0.3s ease;
            border-radius: 16px;
        }

        .feature-pill:hover .pill-glow {
            opacity: 1;
        }

        .feature-pill.paper2code .pill-glow {
            background: linear-gradient(135deg, rgba(100, 181, 246, 0.15), rgba(77, 208, 225, 0.1));
        }

        .feature-pill.text2web .pill-glow {
            background: linear-gradient(135deg, rgba(129, 199, 132, 0.15), rgba(186, 104, 200, 0.1));
        }

        .feature-pill.text2backend .pill-glow {
            background: linear-gradient(135deg, rgba(255, 193, 7, 0.15), rgba(255, 87, 34, 0.1));
        }

        .feature-pill.coderag .pill-glow {
            background: linear-gradient(135deg, rgba(186, 104, 200, 0.15), rgba(156, 39, 176, 0.1));
        }

        .feature-icon {
            font-size: 2.5rem;
            margin-bottom: 1rem;
            display: block;
            text-align: center;
            filter: drop-shadow(0 0 10px rgba(255, 255, 255, 0.3));
        }

        .feature-info h3 {
            font-size: 1.3rem;
            font-weight: 600;
            color: var(--text-primary);
            margin: 0.5rem 0;
            text-align: center;
        }

        .feature-info p {
            font-size: 0.9rem;
            color: var(--text-secondary);
            margin: 0;
            text-align: center;
            opacity: 0.8;
        }

        .feature-status {
            position: absolute;
            top: 1rem;
            right: 1rem;
            background: linear-gradient(135deg, var(--neon-cyan), var(--neon-blue));
            color: white;
            padding: 0.3rem 0.8rem;
            border-radius: 12px;
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            box-shadow: 0 4px 15px rgba(77, 208, 225, 0.3);
        }

        .feature-pill.text2web .feature-status {
            background: linear-gradient(135deg, var(--neon-green), var(--neon-purple));
        }

        .feature-pill.text2backend .feature-status {
            background: linear-gradient(135deg, #ffc107, #ff5722);
        }

        .feature-pill.coderag .feature-status {
            background: linear-gradient(135deg, var(--neon-purple), #9c27b0);
        }

        .workflow-indicator {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 1rem;
            margin-top: 2rem;
            padding: 1rem;
            background: rgba(255, 255, 255, 0.02);
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }

        .workflow-step {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 0.5rem;
            padding: 0.8rem;
            border-radius: 10px;
            transition: all 0.3s ease;
            position: relative;
        }

        .workflow-step.active {
            background: rgba(77, 208, 225, 0.1);
            border: 1px solid rgba(77, 208, 225, 0.3);
        }

        .workflow-step:hover {
            background: rgba(255, 255, 255, 0.05);
            transform: translateY(-2px);
        }

        .step-icon {
            font-size: 1.5rem;
            filter: drop-shadow(0 0 8px rgba(255, 255, 255, 0.2));
        }

        .step-label {
            font-size: 0.8rem;
            color: var(--text-secondary);
            font-weight: 500;
        }

        .workflow-arrow {
            color: var(--neon-cyan);
            font-size: 1.2rem;
            opacity: 0.6;
        }

        /* Responsive adjustments for compact features */
        @media (max-width: 768px) {
            .features-grid {
                grid-template-columns: 1fr;
                gap: 1rem;
            }

            .workflow-indicator {
                flex-wrap: wrap;
                gap: 0.5rem;
            }

            .workflow-arrow {
                display: none;
            }

            .platform-title {
                font-size: 1.5rem;
            }
        }

        /* NEW VERTICAL LAYOUT FEATURE CARDS (LEGACY) */
        .feature-card-vertical {
            position: relative;
            background: linear-gradient(135deg, var(--card-bg) 0%, rgba(45, 55, 72, 0.8) 100%);
            backdrop-filter: blur(25px);
            border: 1px solid var(--border-color);
            padding: 0;
            border-radius: 24px;
            margin: 2.5rem 0;
            transition: all 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            box-shadow: 0 12px 60px rgba(0, 0, 0, 0.4);
            overflow: hidden;
            min-height: 500px;
        }

        .feature-card-vertical:hover {
            transform: translateY(-8px) scale(1.01);
            box-shadow: 0 20px 80px rgba(0, 0, 0, 0.5);
        }

        /* Card glow effect for vertical cards */
        .card-glow-vertical {
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle, transparent 30%, rgba(77, 208, 225, 0.03) 60%, transparent 80%);
            opacity: 0;
            transition: opacity 0.5s ease;
            pointer-events: none;
            animation: verticalGlowPulse 8s ease-in-out infinite;
        }

        .feature-card-vertical:hover .card-glow-vertical {
            opacity: 1;
        }

        @keyframes verticalGlowPulse {
            0%, 100% {
                transform: rotate(0deg) scale(1);
                opacity: 0.3;
            }
            50% {
                transform: rotate(180deg) scale(1.1);
                opacity: 0.7;
            }
        }

        /* Feature header section */
        .feature-header {
            display: flex;
            align-items: center;
            padding: 2.5rem 3rem 1.5rem 3rem;
            background: linear-gradient(135deg, rgba(77, 208, 225, 0.08) 0%, rgba(186, 104, 200, 0.06) 100%);
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            gap: 2rem;
        }

        .feature-logo-container {
            position: relative;
            display: flex;
            align-items: center;
            justify-content: center;
            width: 80px;
            height: 80px;
            flex-shrink: 0;
        }

        .feature-icon-large {
            font-size: 3.5rem;
            z-index: 2;
            filter: drop-shadow(0 0 15px rgba(77, 208, 225, 0.5));
        }

        .feature-header-content {
            flex: 1;
        }

        .feature-title-large {
            font-family: 'Inter', sans-serif !important;
            color: var(--text-primary) !important;
            font-size: 2rem !important;
            font-weight: 700 !important;
            margin-bottom: 0.5rem !important;
            text-shadow: 0 0 20px rgba(255, 255, 255, 0.3);
            background: linear-gradient(135deg, var(--neon-cyan), var(--neon-blue));
            background-clip: text;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .feature-subtitle {
            color: var(--text-secondary) !important;
            font-size: 1rem !important;
            font-weight: 500 !important;
            opacity: 0.9;
        }

        .feature-stats {
            display: flex;
            flex-direction: column;
            gap: 1rem;
            align-items: flex-end;
        }

        .stat-item {
            text-align: center;
            padding: 0.8rem 1.2rem;
            background: rgba(77, 208, 225, 0.1);
            border: 1px solid rgba(77, 208, 225, 0.3);
            border-radius: 12px;
            backdrop-filter: blur(10px);
            min-width: 80px;
        }

        .stat-number {
            display: block;
            font-family: 'JetBrains Mono', monospace !important;
            color: var(--neon-cyan) !important;
            font-size: 1.5rem !important;
            font-weight: 700 !important;
            text-shadow: 0 0 10px rgba(77, 208, 225, 0.5);
        }

        .stat-label {
            display: block;
            color: var(--text-secondary) !important;
            font-size: 0.75rem !important;
            font-weight: 500 !important;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 0.2rem;
        }

        /* Feature content section */
        .feature-content {
            display: flex;
            padding: 2.5rem 3rem;
            gap: 3rem;
            align-items: flex-start;
        }

        .content-left {
            flex: 1.2;
        }

        .content-right {
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: center;
        }

        .feature-description-large {
            color: var(--text-secondary) !important;
            font-size: 1.1rem !important;
            line-height: 1.7 !important;
            font-weight: 500 !important;
            margin-bottom: 2rem;
        }

        /* Card glow effect */
        .card-glow {
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle, transparent 20%, rgba(77, 208, 225, 0.05) 50%, transparent 70%);
            opacity: 0;
            transition: opacity 0.4s ease;
            pointer-events: none;
        }

        .feature-card:hover .card-glow {
            opacity: 1;
            animation: glowRotate 3s linear infinite;
        }

        @keyframes glowRotate {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        /* Different themed card styles */
        .feature-card.primary {
            border-color: var(--neon-cyan);
            background: linear-gradient(135deg,
                rgba(77, 208, 225, 0.1) 0%,
                rgba(45, 55, 72, 0.95) 30%);
        }

        .feature-card.primary:hover {
            transform: translateY(-8px) scale(1.02);
            border-color: var(--neon-cyan);
            box-shadow:
                0 20px 60px rgba(77, 208, 225, 0.3),
                0 0 50px rgba(77, 208, 225, 0.2);
        }

        .feature-card.secondary {
            border-color: var(--neon-purple);
            background: linear-gradient(135deg,
                rgba(186, 104, 200, 0.1) 0%,
                rgba(45, 55, 72, 0.95) 30%);
        }

        .feature-card.secondary:hover {
            transform: translateY(-8px) scale(1.02);
            border-color: var(--neon-purple);
            box-shadow:
                0 20px 60px rgba(186, 104, 200, 0.3),
                0 0 50px rgba(186, 104, 200, 0.2);
        }

        .feature-card.accent {
            border-color: var(--neon-green);
            background: linear-gradient(135deg,
                rgba(129, 199, 132, 0.1) 0%,
                rgba(45, 55, 72, 0.95) 30%);
        }

        .feature-card.accent:hover {
            transform: translateY(-8px) scale(1.02);
            border-color: var(--neon-green);
            box-shadow:
                0 20px 60px rgba(129, 199, 132, 0.3),
                0 0 50px rgba(129, 199, 132, 0.2);
        }

        .feature-card.tech {
            border-color: var(--neon-blue);
            background: linear-gradient(135deg,
                rgba(100, 181, 246, 0.1) 0%,
                rgba(45, 55, 72, 0.95) 30%);
        }

        .feature-card.tech:hover {
            transform: translateY(-8px) scale(1.02);
            border-color: var(--neon-blue);
            box-shadow:
                0 20px 60px rgba(100, 181, 246, 0.3),
                0 0 50px rgba(100, 181, 246, 0.2);
        }

        /* Feature icons */
        .feature-icon {
            font-size: 3rem;
            margin-bottom: 1rem;
            text-align: center;
            filter: drop-shadow(0 0 10px rgba(77, 208, 225, 0.5));
            flex-shrink: 0;
        }

        /* Feature titles */
        .feature-title {
            font-family: 'Inter', sans-serif !important;
            color: var(--text-primary) !important;
            font-size: 1.3rem !important;
            font-weight: 700 !important;
            margin-bottom: 1rem !important;
            text-align: center;
            text-shadow: 0 0 15px rgba(255, 255, 255, 0.3);
            flex-shrink: 0;
        }

        /* Feature descriptions */
        .feature-description {
            color: var(--text-secondary) !important;
            line-height: 1.6 !important;
            font-weight: 500 !important;
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        /* Typing animation effect */
        .typing-text {
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 0.95rem !important;
            margin-bottom: 1.5rem;
            border-right: 2px solid var(--neon-cyan);
            white-space: nowrap;
            overflow: hidden;
            animation: typing 3s steps(60, end), blink 1s infinite;
        }

        @keyframes typing {
            from { width: 0; }
            to { width: 100%; }
        }

        @keyframes blink {
            0%, 50% { border-color: var(--neon-cyan); }
            51%, 100% { border-color: transparent; }
        }

        /* Technology tags */
        .tech-specs {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 1rem;
        }

        .spec-tag {
            background: linear-gradient(135deg, var(--neon-cyan), var(--neon-blue));
            color: #000 !important;
            padding: 0.3rem 0.8rem;
            border-radius: 15px;
            font-size: 0.8rem;
            font-weight: 600;
            letter-spacing: 0.5px;
            box-shadow: 0 2px 10px rgba(77, 208, 225, 0.3);
        }

        /* Progress bar animation */
        .progress-bar {
            width: 100%;
            height: 6px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
            overflow: hidden;
            margin-top: 1rem;
        }

        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--neon-purple), var(--neon-cyan), var(--neon-green));
            background-size: 200% 100%;
            border-radius: 3px;
            animation: progressMove 2s ease-in-out infinite;
            width: 75%;
        }

        @keyframes progressMove {
            0% { background-position: -200% 0; }
            100% { background-position: 200% 0; }
        }

        /* Code preview area */
        .code-preview {
            background: rgba(0, 0, 0, 0.4);
            border: 1px solid var(--neon-green);
            border-radius: 10px;
            padding: 1rem;
            margin-top: 1rem;
            font-family: 'JetBrains Mono', monospace;
        }

        .code-line {
            font-size: 0.85rem;
            line-height: 1.6;
            margin-bottom: 0.5rem;
            color: var(--neon-green) !important;
        }

        .code-line.generating {
            color: var(--neon-cyan) !important;
            animation: textGlow 2s ease-in-out infinite;
        }

        @keyframes textGlow {
            0%, 100% { text-shadow: 0 0 5px var(--neon-cyan); }
            50% { text-shadow: 0 0 15px var(--neon-cyan), 0 0 25px var(--neon-cyan); }
        }

        /* Progress dots */
        .code-progress {
            margin-top: 1rem;
        }

        .progress-dots {
            display: flex;
            gap: 0.5rem;
            justify-content: center;
        }

        .dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.3);
            transition: all 0.3s ease;
        }

        .dot.active {
            background: var(--neon-green);
            box-shadow: 0 0 10px var(--neon-green);
            animation: dotPulse 1.5s ease-in-out infinite;
        }

        @keyframes dotPulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.3); }
        }

        /* Technology stack display */
        .tech-stack {
            display: flex;
            flex-direction: column;
            gap: 0.8rem;
        }

        .stack-item {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            padding: 0.8rem;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            transition: all 0.3s ease;
        }

        .stack-item:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: var(--neon-blue);
            transform: translateX(5px);
        }

        .stack-icon {
            font-size: 1.2rem;
            filter: drop-shadow(0 0 8px rgba(100, 181, 246, 0.6));
        }

        .stack-name {
            font-family: 'JetBrains Mono', monospace !important;
            color: var(--text-primary) !important;
            font-weight: 600 !important;
            font-size: 0.9rem;
        }

        /* Responsive design */
        @media (max-width: 768px) {
            .main-header {
                padding: 2.5rem 1.5rem;
                margin-bottom: 2rem;
                border-radius: 20px;
            }

            .main-header h1 {
                font-size: 2.5rem !important;
            }

            .main-header h3 {
                font-size: 1rem !important;
                letter-spacing: 1.5px !important;
            }

            .main-header p {
                font-size: 0.8rem !important;
                letter-spacing: 0.5px !important;
            }

            .ai-capabilities-section {
                padding: 1.5rem 1rem;
                margin: 1.5rem 0;
                border-radius: 15px;
            }

            .capabilities-title {
                font-size: 1.6rem !important;
            }

            .capabilities-subtitle {
                font-size: 0.8rem !important;
                letter-spacing: 1px !important;
            }

            .feature-card {
                padding: 1.5rem;
                margin: 1rem 0;
                height: auto;
                min-height: 350px;
                border-radius: 15px;
            }

            .neural-network {
                top: 0.5rem;
                right: 1rem;
            }

            .typing-text {
                white-space: normal;
                border-right: none;
                animation: none;
                font-size: 0.85rem !important;
            }

            .feature-icon {
                font-size: 2.5rem;
            }

            .feature-title {
                font-size: 1.1rem !important;
            }
        }
        /* ================================
           CONTENT COMPONENT STYLES
           ================================ */

        /* Feature Flow Animation */
        .feature-flow {
            display: flex;
            align-items: center;
            gap: 1rem;
            flex-wrap: wrap;
            margin-top: 1.5rem;
        }

        .flow-step {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.8rem 1.2rem;
            background: rgba(77, 208, 225, 0.1);
            border: 1px solid rgba(77, 208, 225, 0.3);
            border-radius: 25px;
            transition: all 0.3s ease;
        }

        .flow-step.active {
            background: rgba(77, 208, 225, 0.2);
            border-color: var(--neon-cyan);
            box-shadow: 0 0 15px rgba(77, 208, 225, 0.3);
        }

        .flow-icon {
            font-size: 1.2rem;
        }

        .flow-arrow {
            color: var(--neon-cyan);
            font-size: 1.2rem;
            animation: arrowFlow 2s ease-in-out infinite;
        }

        @keyframes arrowFlow {
            0%, 100% { transform: translateX(0); opacity: 0.7; }
            50% { transform: translateX(5px); opacity: 1; }
        }

        /* Code Simulation */
        .code-simulation {
            background: rgba(0, 0, 0, 0.6);
            border: 1px solid var(--neon-cyan);
            border-radius: 12px;
            padding: 1.5rem;
            font-family: 'JetBrains Mono', monospace;
            width: 100%;
            max-width: 400px;
        }

        .code-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid rgba(77, 208, 225, 0.3);
        }

        .code-lang {
            color: var(--neon-cyan);
            font-weight: 600;
            font-size: 0.9rem;
        }

        .code-status.generating {
            color: var(--neon-green);
            font-size: 0.8rem;
            animation: statusPulse 2s ease-in-out infinite;
        }

        @keyframes statusPulse {
            0%, 100% { opacity: 0.7; }
            50% { opacity: 1; }
        }

        .code-lines {
            display: flex;
            flex-direction: column;
            gap: 0.3rem;
        }

        .code-line {
            color: var(--text-secondary);
            font-size: 0.85rem;
            line-height: 1.4;
            opacity: 0;
        }

        .code-line.typing {
            animation: typeIn 0.8s ease-out forwards;
        }

        .code-line.delay-1 { animation-delay: 0.8s; }
        .code-line.delay-2 { animation-delay: 1.6s; }
        .code-line.delay-3 { animation-delay: 2.4s; }
        .code-line.delay-4 { animation-delay: 3.2s; }

        @keyframes typeIn {
            0% {
                opacity: 0;
                transform: translateX(-10px);
            }
            100% {
                opacity: 1;
                transform: translateX(0);
            }
        }

        /* Agent Grid */
        .agent-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 1rem;
            margin-top: 1.5rem;
        }

        .agent-card {
            padding: 1rem;
            background: rgba(186, 104, 200, 0.1);
            border: 1px solid rgba(186, 104, 200, 0.3);
            border-radius: 12px;
            text-align: center;
            transition: all 0.3s ease;
        }

        .agent-card.active {
            background: rgba(186, 104, 200, 0.2);
            border-color: var(--neon-purple);
            box-shadow: 0 0 15px rgba(186, 104, 200, 0.3);
        }

        .agent-avatar {
            font-size: 1.5rem;
            margin-bottom: 0.5rem;
        }

        .agent-card h4 {
            color: var(--text-primary) !important;
            font-size: 0.9rem !important;
            font-weight: 600 !important;
            margin-bottom: 0.3rem !important;
        }

        .agent-card p {
            color: var(--text-secondary) !important;
            font-size: 0.75rem !important;
            margin: 0 !important;
        }

        /* Collaboration Visualization */
        .collaboration-viz {
            position: relative;
            width: 300px;
            height: 300px;
            margin: 0 auto;
        }

        .collaboration-center {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            text-align: center;
            z-index: 2;
        }

        .center-node {
            width: 60px;
            height: 60px;
            background: linear-gradient(135deg, var(--neon-purple), var(--neon-cyan));
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.5rem;
            margin: 0 auto 0.5rem;
            animation: centerRotate 4s linear infinite;
        }

        .collaboration-center span {
            color: var(--text-primary) !important;
            font-size: 0.8rem !important;
            font-weight: 600 !important;
        }

        .collaboration-agents {
            position: relative;
            width: 100%;
            height: 100%;
        }

        .collab-agent {
            position: absolute;
            width: 50px;
            height: 50px;
            background: rgba(77, 208, 225, 0.2);
            border: 2px solid var(--neon-cyan);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.2rem;
        }

        .pulse-ring {
            position: absolute;
            width: 60px;
            height: 60px;
            border: 2px solid var(--neon-cyan);
            border-radius: 50%;
            top: -5px;
            left: -5px;
            animation: pulseRing 2s ease-out infinite;
        }

        .agent-pos-1 {
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            animation-delay: 0s;
        }

        .agent-pos-2 {
            top: 50%;
            right: 20px;
            transform: translateY(-50%);
            animation-delay: 0.5s;
        }

        .agent-pos-3 {
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            animation-delay: 1s;
        }

        .agent-pos-4 {
            top: 50%;
            left: 20px;
            transform: translateY(-50%);
            animation-delay: 1.5s;
        }

        @keyframes centerRotate {
            0% { transform: translate(-50%, -50%) rotate(0deg); }
            100% { transform: translate(-50%, -50%) rotate(360deg); }
        }

        @keyframes pulseRing {
            0% {
                transform: scale(0.8);
                opacity: 1;
            }
            100% {
                transform: scale(1.4);
                opacity: 0;
            }
        }

        /* Vision Demo */
        .vision-demo {
            margin-top: 1.5rem;
            padding: 1.5rem;
            background: rgba(129, 199, 132, 0.1);
            border: 1px solid rgba(129, 199, 132, 0.3);
            border-radius: 15px;
        }

        .demo-input {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 1rem;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 10px;
            margin-bottom: 1rem;
        }

        .input-icon {
            font-size: 1.5rem;
        }

        .input-text {
            flex: 1;
            color: var(--neon-green) !important;
            font-family: 'JetBrains Mono', monospace !important;
            font-size: 0.9rem !important;
        }

        .input-text.typing {
            border-right: 2px solid var(--neon-green);
            animation: inputTyping 4s steps(60, end), inputBlink 1s infinite;
        }

        @keyframes inputTyping {
            from { width: 0; }
            to { width: 100%; }
        }

        @keyframes inputBlink {
            0%, 50% { border-color: var(--neon-green); }
            51%, 100% { border-color: transparent; }
        }

        .demo-arrow {
            text-align: center;
            font-size: 1.5rem;
            color: var(--neon-green);
            margin: 1rem 0;
            animation: arrowBounce 2s ease-in-out infinite;
        }

        @keyframes arrowBounce {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(5px); }
        }

        .demo-output {
            padding: 1rem;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 10px;
        }

        .output-items {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .output-item {
            padding: 0.5rem 1rem;
            background: rgba(129, 199, 132, 0.2);
            border: 1px solid rgba(129, 199, 132, 0.4);
            border-radius: 8px;
            color: var(--neon-green) !important;
            font-size: 0.85rem !important;
            animation: itemAppear 0.8s ease-out forwards;
            opacity: 0;
        }

        .output-item:nth-child(1) { animation-delay: 0.5s; }
        .output-item:nth-child(2) { animation-delay: 1s; }
        .output-item:nth-child(3) { animation-delay: 1.5s; }
        .output-item:nth-child(4) { animation-delay: 2s; }

        @keyframes itemAppear {
            0% {
                opacity: 0;
                transform: translateX(-20px);
            }
            100% {
                opacity: 1;
                transform: translateX(0);
            }
        }

        /* Future Timeline */
        .future-timeline {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
            padding: 1rem;
            max-width: 300px;
        }

        .timeline-item {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 1rem;
            border-radius: 12px;
            transition: all 0.3s ease;
        }

        .timeline-item.completed {
            background: rgba(77, 208, 225, 0.1);
            border: 1px solid rgba(77, 208, 225, 0.3);
        }

        .timeline-item.active {
            background: rgba(129, 199, 132, 0.1);
            border: 1px solid rgba(129, 199, 132, 0.3);
            box-shadow: 0 0 15px rgba(129, 199, 132, 0.3);
        }

        .timeline-item.future {
            background: rgba(186, 104, 200, 0.1);
            border: 1px solid rgba(186, 104, 200, 0.3);
            opacity: 0.7;
        }

        .timeline-marker {
            width: 40px;
            height: 40px;
            background: var(--card-bg);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.2rem;
            flex-shrink: 0;
        }

        .timeline-content h4 {
            color: var(--text-primary) !important;
            font-size: 1rem !important;
            font-weight: 600 !important;
            margin-bottom: 0.2rem !important;
        }

        .timeline-content p {
            color: var(--text-secondary) !important;
            font-size: 0.8rem !important;
            margin: 0 !important;
        }

        /* Community Features */
        .community-features {
            display: flex;
            flex-direction: column;
            gap: 1rem;
            margin-top: 1.5rem;
        }

        .community-feature {
            display: flex;
            align-items: flex-start;
            gap: 1rem;
            padding: 1rem;
            background: rgba(100, 181, 246, 0.1);
            border: 1px solid rgba(100, 181, 246, 0.3);
            border-radius: 12px;
            transition: all 0.3s ease;
        }

        .community-feature:hover {
            background: rgba(100, 181, 246, 0.15);
            border-color: var(--neon-blue);
        }

        .feature-icon-small {
            font-size: 1.5rem;
            flex-shrink: 0;
            margin-top: 0.2rem;
        }

        .feature-text h4 {
            color: var(--text-primary) !important;
            font-size: 1rem !important;
            font-weight: 600 !important;
            margin-bottom: 0.3rem !important;
        }

        .feature-text p {
            color: var(--text-secondary) !important;
            font-size: 0.85rem !important;
            line-height: 1.4 !important;
            margin: 0 !important;
        }

        /* Tech Ecosystem */
        .tech-ecosystem {
            position: relative;
            width: 300px;
            height: 300px;
            margin: 0 auto;
        }

        .ecosystem-center {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            text-align: center;
            z-index: 2;
        }

        .center-logo {
            width: 60px;
            height: 60px;
            background: linear-gradient(135deg, var(--neon-blue), var(--neon-cyan));
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.5rem;
            margin: 0 auto 0.5rem;
            animation: logoFloat 3s ease-in-out infinite;
        }

        .ecosystem-center span {
            color: var(--text-primary) !important;
            font-size: 0.9rem !important;
            font-weight: 600 !important;
        }

        .ecosystem-ring {
            position: relative;
            width: 100%;
            height: 100%;
        }

        .ecosystem-item {
            position: absolute;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 0.3rem;
            padding: 0.8rem;
            background: rgba(100, 181, 246, 0.1);
            border: 1px solid rgba(100, 181, 246, 0.3);
            border-radius: 12px;
            animation: ecosystemOrbit 8s linear infinite;
        }

        .ecosystem-item.item-1 {
            top: 10px;
            left: 50%;
            transform: translateX(-50%);
        }

        .ecosystem-item.item-2 {
            top: 50%;
            right: 10px;
            transform: translateY(-50%);
        }

        .ecosystem-item.item-3 {
            bottom: 10px;
            left: 50%;
            transform: translateX(-50%);
        }

        .ecosystem-item.item-4 {
            top: 50%;
            left: 10px;
            transform: translateY(-50%);
        }

        .item-icon {
            font-size: 1.2rem;
        }

        .ecosystem-item span {
            color: var(--text-primary) !important;
            font-size: 0.7rem !important;
            font-weight: 600 !important;
            text-align: center;
        }

        @keyframes logoFloat {
            0%, 100% {
                transform: translate(-50%, -50%) scale(1);
            }
            50% {
                transform: translate(-50%, -50%) scale(1.05);
            }
        }

        @keyframes ecosystemOrbit {
            0% {
                box-shadow: 0 0 10px rgba(100, 181, 246, 0.3);
            }
            50% {
                box-shadow: 0 0 20px rgba(100, 181, 246, 0.5);
            }
            100% {
                box-shadow: 0 0 10px rgba(100, 181, 246, 0.3);
            }
        }

        /* Responsive adjustments for vertical cards */
        @media (max-width: 768px) {
            .feature-content {
                flex-direction: column;
                gap: 2rem;
            }

            .feature-header {
                flex-direction: column;
                text-align: center;
                gap: 1rem;
            }

            .feature-stats {
                flex-direction: row;
                justify-content: center;
            }

            .collaboration-viz,
            .tech-ecosystem {
                width: 250px;
                height: 250px;
            }
        }

        /* Additional sidebar styles for newer Streamlit versions */
        /* Cover any new sidebar containers or data-testid selectors */
        [data-testid="stSidebar"],
        [data-testid="stSidebarNav"],
        [data-testid="stSidebarNavItems"],
        .stSidebar,
        .sidebar,
        .sidebar-content,
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #0d1117 0%, #161b22 50%, #21262d 100%) !important;
            border-right: 2px solid var(--neon-cyan) !important;
            box-shadow: 0 0 20px rgba(77, 208, 225, 0.3) !important;
            color: var(--text-primary) !important;
        }

        /* Light mode override for new sidebar selectors */
        @media (prefers-color-scheme: light) {
            [data-testid="stSidebar"],
            [data-testid="stSidebarNav"],
            [data-testid="stSidebarNavItems"],
            .stSidebar,
            .sidebar,
            .sidebar-content,
            section[data-testid="stSidebar"] {
                background: linear-gradient(180deg, #0d1117 0%, #161b22 50%, #21262d 100%) !important;
                border-right: 2px solid var(--neon-cyan) !important;
                box-shadow: 0 0 20px rgba(77, 208, 225, 0.3) !important;
                color: var(--text-primary) !important;
            }
        }

        /* Alternative light theme detection for new sidebar selectors */
        [data-theme="light"] [data-testid="stSidebar"],
        [data-theme="light"] [data-testid="stSidebarNav"],
        [data-theme="light"] [data-testid="stSidebarNavItems"],
        [data-theme="light"] .stSidebar,
        [data-theme="light"] .sidebar,
        [data-theme="light"] .sidebar-content,
        [data-theme="light"] section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #0d1117 0%, #161b22 50%, #21262d 100%) !important;
            border-right: 2px solid var(--neon-cyan) !important;
            box-shadow: 0 0 20px rgba(77, 208, 225, 0.3) !important;
            color: var(--text-primary) !important;
        }

        /* Force all text in sidebar containers to use dark theme colors */
        [data-testid="stSidebar"] *,
        [data-testid="stSidebarNav"] *,
        [data-testid="stSidebarNavItems"] *,
        .stSidebar *,
        .sidebar *,
        .sidebar-content *,
        section[data-testid="stSidebar"] * {
            color: var(--text-primary) !important;
        }

        /* Light mode: Force all text in sidebar containers to use dark theme colors */
        @media (prefers-color-scheme: light) {
            [data-testid="stSidebar"] *,
            [data-testid="stSidebarNav"] *,
            [data-testid="stSidebarNavItems"] *,
            .stSidebar *,
            .sidebar *,
            .sidebar-content *,
            section[data-testid="stSidebar"] * {
                color: var(--text-primary) !important;
            }
        }

        /* Alternative light theme detection for sidebar text */
        [data-theme="light"] [data-testid="stSidebar"] *,
        [data-theme="light"] [data-testid="stSidebarNav"] *,
        [data-theme="light"] [data-testid="stSidebarNavItems"] *,
        [data-theme="light"] .stSidebar *,
        [data-theme="light"] .sidebar *,
        [data-theme="light"] .sidebar-content *,
        [data-theme="light"] section[data-testid="stSidebar"] * {
            color: var(--text-primary) !important;
        }

        /* ====================================================================
           MODERN HEADER DESIGN - MINIMALIST & PROFESSIONAL
           ==================================================================== */

        .main-header-modern {
            text-align: center;
            padding: 2rem 1rem 1.5rem 1rem;
            margin-bottom: 1rem;
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.02) 0%, rgba(255, 255, 255, 0.08) 100%);
            border-radius: 16px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
        }

        .header-logo {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 1rem;
            margin-bottom: 1rem;
        }

        .logo-icon {
            font-size: 3rem;
            filter: drop-shadow(0 0 20px rgba(100, 181, 246, 0.6));
            animation: float 3s ease-in-out infinite;
        }

        @keyframes float {
            0%, 100% { transform: translateY(0px); }
            50% { transform: translateY(-5px); }
        }

        .logo-text h1 {
            margin: 0;
            font-size: 2.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--neon-blue), var(--neon-cyan), var(--neon-purple));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            text-shadow: 0 0 30px rgba(100, 181, 246, 0.3);
        }

        .logo-tagline {
            display: block;
            font-size: 0.9rem;
            color: var(--text-muted);
            font-weight: 400;
            opacity: 0.8;
            margin-top: 0.3rem;
        }

        .header-subtitle {
            display: flex;
            flex-direction: column;
            gap: 0.3rem;
            opacity: 0.7;
        }

        .org-info {
            font-size: 0.8rem;
            color: var(--neon-cyan);
            font-weight: 600;
            letter-spacing: 1px;
            text-transform: uppercase;
        }

        .mission {
            font-size: 0.85rem;
            color: var(--text-muted);
            font-weight: 400;
            font-style: italic;
        }

        @media (max-width: 768px) {
            .main-header-modern {
                padding: 1.5rem 1rem;
            }

            .header-logo {
                flex-direction: column;
                gap: 0.5rem;
            }

            .logo-icon {
                font-size: 2.5rem;
            }

            .logo-text h1 {
                font-size: 2rem;
            }

            .header-subtitle {
                gap: 0.2rem;
            }

            .org-info {
                font-size: 0.75rem;
            }

            .mission {
                font-size: 0.8rem;
            }
        }

        /* ====================================================================
           COMPACT FEATURES SECTION - MODERN MINIMALIST DESIGN
           ==================================================================== */

        .features-compact-header {
            text-align: center;
            margin: 2rem 0 1.5rem 0;
            padding: 0;
        }

        .features-title {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 1rem;
            flex-wrap: wrap;
        }

        .features-icon {
            font-size: 2rem;
            filter: drop-shadow(0 0 10px rgba(100, 181, 246, 0.6));
        }

        .features-title h3 {
            margin: 0;
            font-size: 1.8rem;
            font-weight: 600;
            color: var(--text-primary);
            background: linear-gradient(135deg, var(--neon-blue), var(--neon-cyan));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .features-subtitle {
            font-size: 0.95rem;
            color: var(--text-muted);
            font-weight: 400;
            opacity: 0.8;
        }

        .features-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1rem;
            margin: 1rem 0;
            padding: 0;
        }

        @media (max-width: 768px) {
            .features-row {
                grid-template-columns: 1fr;
                gap: 0.8rem;
            }
        }

        .feature-card-compact {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 1.2rem;
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
            cursor: pointer;
        }

        .feature-card-compact::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.08), transparent);
            transition: left 0.6s ease;
        }

        .feature-card-compact:hover {
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.2);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15);
            background: rgba(255, 255, 255, 0.08);
        }

        .feature-card-compact:hover::before {
            left: 100%;
        }

        .feature-card-compact .feature-icon {
            flex-shrink: 0;
            width: 48px;
            height: 48px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.5rem;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            transition: all 0.3s ease;
        }

        .feature-card-compact:hover .feature-icon {
            transform: scale(1.1);
            background: rgba(255, 255, 255, 0.15);
        }

        /* Specific color themes for each card */
        .feature-card-compact.paper2code .feature-icon {
            background: linear-gradient(135deg, #667eea, #764ba2);
        }

        .feature-card-compact.text2web .feature-icon {
            background: linear-gradient(135deg, #4facfe, #00f2fe);
        }

        .feature-card-compact.text2backend .feature-icon {
            background: linear-gradient(135deg, #fa709a, #fee140);
        }

        .feature-card-compact.coderag .feature-icon {
            background: linear-gradient(135deg, #a8edea, #fed6e3);
        }

        .feature-content {
            flex-grow: 1;
            min-width: 0;
        }

        .feature-content h4 {
            margin: 0 0 0.3rem 0;
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.2;
        }

        .feature-content p {
            margin: 0 0 0.8rem 0;
            font-size: 0.85rem;
            color: var(--text-muted);
            line-height: 1.3;
            opacity: 0.9;
        }

        .feature-tags {
            display: flex;
            gap: 0.4rem;
            flex-wrap: wrap;
        }

        .feature-tags .tag {
            padding: 0.2rem 0.6rem;
            font-size: 0.75rem;
            background: rgba(100, 181, 246, 0.15);
            color: var(--neon-blue);
            border: 1px solid rgba(100, 181, 246, 0.3);
            border-radius: 20px;
            font-weight: 500;
            transition: all 0.2s ease;
        }

        .feature-card-compact:hover .feature-tags .tag {
            background: rgba(100, 181, 246, 0.25);
            border-color: rgba(100, 181, 246, 0.5);
        }

        .feature-status {
            flex-shrink: 0;
            display: flex;
            align-items: center;
        }

        .status-indicator {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--neon-green);
            box-shadow: 0 0 10px rgba(129, 199, 132, 0.6);
            animation: pulse-status 2s infinite;
        }

        @keyframes pulse-status {
            0%, 100% {
                opacity: 1;
                transform: scale(1);
            }
            50% {
                opacity: 0.7;
                transform: scale(1.2);
            }
        }

        /* Responsive adjustments */
        @media (max-width: 640px) {
            .features-title {
                flex-direction: column;
                gap: 0.5rem;
            }

            .features-title h3 {
                font-size: 1.5rem;
            }

            .feature-card-compact {
                padding: 1rem;
                gap: 0.8rem;
            }

            .feature-card-compact .feature-icon {
                width: 40px;
                height: 40px;
                font-size: 1.3rem;
            }

            .feature-content h4 {
                font-size: 1rem;
            }

            .feature-content p {
                font-size: 0.8rem;
            }

            .feature-tags .tag {
                font-size: 0.7rem;
                padding: 0.15rem 0.5rem;
            }
        }

    </style>
    """
