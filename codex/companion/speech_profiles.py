"""Reviewed multilingual CPU profiles; approximate download bytes exclude Python dependencies."""
PROFILES = {
    'tiny': {'repo': 'Systran/faster-whisper-tiny', 'revision': 'd90ca5fe260221311c53c58e660288d3deb8d356', 'download_mb': 78, 'label': 'Tiny · 最小'},
    'base': {'repo': 'Systran/faster-whisper-base', 'revision': 'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66', 'download_mb': 148, 'label': 'Base · 轻量'},
    'small': {'repo': 'Systran/faster-whisper-small', 'revision': '536b0662742c02347bc0e980a01041f333bce120', 'download_mb': 486, 'label': 'Small · 较大'},
}


def profile_for(config):
    if config.get('profile') in PROFILES:
        return config['profile']
    name = str(config.get('model', '')).replace('\\', '/').split('/')[-1]
    return next((key for key, p in PROFILES.items() if p['revision'] == name), 'existing')
