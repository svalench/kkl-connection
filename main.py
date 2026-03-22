"""
Точка входа: подключение к VAG KKL, эмулятор ELM323, вывод в терминал
доступных контроллеров и текущих параметров в человекочитаемом виде.
"""
import argparse
import logging

from kkl import configure_ftdi_port
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
        default=10400,
        help="Скорость порта (10400 для K-Line по стандарту, 9600 — для некоторых авто)",
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
    parser.add_argument(
        "--no-fast-init",
        action="store_true",
        help="Только ISO 9141-2 slow init (5-baud), без KWP2000 fast init",
    )
    parser.add_argument(
        "--pids",
        type=str,
        default=None,
        help="Список PID/сервисов через запятую (напр. 010D,010C,03)",
    )
    parser.add_argument(
        "--latency",
        action="store_true",
        help="Попытаться выставить FTDI USB latency timer = 1 ms (Linux / подсказка Windows)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.latency:
        configure_ftdi_port(args.port)

    pids_list = None
    if args.pids:
        pids_list = [p.strip().upper().replace(" ", "") for p in args.pids.split(",") if p.strip()]

    emulator = ELM323Emulator(
        port=args.port,
        baud=args.baud,
        verbose=args.verbose,
        try_fast_init=not args.no_fast_init,
    )
    try:
        print("Подключение к KKL на порту %s (%d бод)" % (args.port, args.baud))
        if args.verbose:
            print("Режим verbose: подробные логи включены")
        run_display_loop(emulator, interval_sec=args.interval, pids=pids_list)
    except KeyboardInterrupt:
        pass
    finally:
        emulator.close()
        print("Соединение закрыто.")


if __name__ == "__main__":
    main()
