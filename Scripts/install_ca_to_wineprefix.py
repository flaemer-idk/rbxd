#!/usr/bin/env python3
'''
Вшивает CA сертификат вебсервера rbxd в реестр wine-префикса.

Зачем: Studio (и вообще всё, что ходит через wininet/schannel в wine) под
Linux валидирует HTTPS-цепочку. Без доверенного корня v463-студия не грузит
НИКАКИЕ ассеты. Webserver rbxd с реврайта 2026-09 держит стабильный
сертификат в `<rbxd>/data/ssl/` — CA вшивается ОДИН раз на префикс.

Скрипт идемпотентен: старая запись RobloxLocalCA удаляется и пишется заново,
повторные запуски не плодят мусор.

ВАЖНО: закрой все wine/umu процессы перед запуском — живой wineserver при
выходе перезапишет user.reg и затрёт изменения.

Использование:
    python3 scripts/install_ca_to_wineprefix.py                # авто-поиск префикса
    python3 scripts/install_ca_to_wineprefix.py --prefix ~/Games/umu/umu-default/pfx
    python3 scripts/install_ca_to_wineprefix.py --ca /custom/path/ca.pem
'''
import argparse
import os
import re
import ssl
import sys

SECTION_NAME = 'RobloxLocalCA'
SECTION_HEADER = (
    '[Software\\\\Microsoft\\\\SystemCertificates\\\\Root'
    '\\\\Certificates\\\\' + SECTION_NAME + ']'
)


def default_ca_path() -> str:
    top = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(top, 'data', 'ssl', 'ca.pem')


def find_prefixes() -> list[str]:
    candidates = []
    env = os.environ.get('WINEPREFIX')
    if env:
        candidates.append(env)
    candidates += [
        os.path.expanduser('~/.local/share/umu/umu-default/pfx'),
        os.path.expanduser('~/Games/umu/umu-default/pfx'),
        os.path.expanduser('~/.wine'),
    ]
    return [c for c in candidates if os.path.isfile(os.path.join(c, 'user.reg'))]


def remove_old_section(text: str) -> str:
    '''
    Удаляет прошлый блок RobloxLocalCA (заголовок + строки до следующего
    заголовка секции или конца файла), чтобы повторный запуск не плодил дубли.
    '''
    pattern = re.compile(
        r'\n*' + re.escape(SECTION_HEADER) + r'\n(?:(?!\n\[)[^\n]*\n?)*'
    )
    return pattern.sub('', text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--prefix',
        help='wine-префикс (каталог с user.reg). По умолчанию — авто-поиск.',
    )
    parser.add_argument(
        '--ca', default=default_ca_path(),
        help='PEM файл CA (по умолчанию: %(default)s)',
    )
    args = parser.parse_args()

    prefixes = [args.prefix] if args.prefix else find_prefixes()
    prefixes = [
        p for p in prefixes
        if p and os.path.isfile(os.path.join(p, 'user.reg'))
    ]
    if not prefixes:
        print('Не нашёл wine-префикс с user.reg. Укажи --prefix.')
        return 1

    if not os.path.isfile(args.ca):
        print(f'CA не найден: {args.ca}')
        print('Сначала один раз запусти вебсервер rbxd (даже на пару секунд) —')
        print('он сгенерит стабильный сертификат в data/ssl/.')
        return 1

    with open(args.ca, 'r') as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    hex_string = ','.join(f'{b:02x}' for b in der)

    entry = (
        '\n\n' + SECTION_HEADER + '\n'
        '"Blob"=hex:03,00,00,00,01,00,00,00,00,00,00,00,' + hex_string + '\n'
    )

    for prefix in prefixes:
        reg_path = os.path.join(prefix, 'user.reg')
        with open(reg_path, 'r', encoding='utf-8') as f:
            text = f.read()

        cleaned = remove_old_section(text)
        if cleaned != text:
            print(f'{reg_path}: старая запись {SECTION_NAME} удалена')
        with open(reg_path, 'w', encoding='utf-8') as f:
            f.write(cleaned + entry)
        print(f'{reg_path}: CA вшит ({SECTION_NAME})')

    print('Готово. Если wineserver был запущен — перезапусти префикс и повтори.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
