"""
Точка входа: подключение к VAG KKL, эмулятор ELM323, вывод в терминал
доступных контроллеров и текущих параметров в человекочитаемом виде.
"""
import argparse

from elm323_emulator import ELM323Emulator
from obd_display import run_display_loop


def main():
    parser = argparse.ArgumentParser(
        description="Опрос авто через VAG KKL: контроллеры и параметры в терминале"
    )
    parser.add_argument(
        "-p", "--port",
        default="COM1",
        help="Порт KKL (COM1, /dev/ttyUSB0, ...)",
    )
    parser.add_argument(
        "-b", "--baud",
        type=int,
        default=9600,
        help="Скорость порта (9600 или 10400 для VAG, по умолчанию 9600)",
    )
    parser.add_argument(
        "-i", "--interval",
        type=float,
        default=1.0,
        help="Интервал опроса в секундах (по умолчанию 1.0)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Подробные логи подключения и обмена",
    )
    args = parser.parse_args()

    emulator = ELM323Emulator(
        port=args.port, baud=args.baud, verbose=args.verbose
    )
    try:
        print("Подключение к KKL на порту %s (%d бод)" % (args.port, args.baud))
        if args.verbose:
            print("Режим verbose: подробные логи включены")
        run_display_loop(emulator, interval_sec=args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        emulator.close()
        print("Соединение закрыто.")


if __name__ == "__main__":
    main()
