import serial
import time

PORT = 'COM1'
BAUD_10400 = 9600


def send_5_baud_init(port_name):
    """Инициализация ISO 9141-2 на скорости 5 бод"""
    print("[*] Попытка ISO 9141-2 (5-Baud Init)...")

    # Открываем порт на 5 бод специально для "пробуждения"
    # 8 бит данных, 1 стоп-бит, без четности
    ser_init = serial.Serial(port_name, 5, timeout=2)

    # Байт адреса для стандартного OBD2 по ISO 9141-2 это 0x33
    address_byte = b'\x33'
    ser_init.write(address_byte)
    ser_init.flush()

    # Сразу закрываем, чтобы переоткрыть на нормальной скорости
    ser_init.close()
    time.sleep(0.1)


def run_iso_9141_handshake(ser):
    """
    После 5-baud init ЭБУ должен прислать 0x55 и два ключевых байта.
    Мы должны их прочитать и вернуть инвертированный последний байт.
    """
    ser.timeout = 1.5
    # Ждем 0x55 (Sync byte)
    sync = ser.read(1)
    if sync != b'\x55':
        print(f"[-] ISO 9141: Неверный байт синхронизации: {sync.hex()}")
        return False

    # Читаем Key Word 1 и Key Word 2
    kw1 = ser.read(1)
    kw2 = ser.read(1)
    print(f"[+] ЭБУ ответил Key Bytes: {kw1.hex()} {kw2.hex()}")

    if kw1 and kw2:
        # Инвертируем KW2 и отправляем обратно ЭБУ для подтверждения
        # По стандарту: тестер инвертирует и отправляет KW2
        inv_kw2 = bytes([kw2[0] ^ 0xFF])
        time.sleep(0.05)
        ser.write(inv_kw2)

        # ЭБУ в ответ должен прислать инвертированный адрес (0x33 -> 0xCC)
        ack = ser.read(1)
        if ack and ack[0] == (0x33 ^ 0xFF):
            print("[!] Связь по ISO 9141-2 установлена!")
            return True
    return False


def main():
    # 1. Сначала пробуем 5-Baud Init (ISO 9141-2)
    send_5_baud_init(PORT)

    ser = serial.Serial(PORT, BAUD_10400, timeout=1)

    # Очищаем эхо от нашей отправки 0x33 на 5 бодах
    ser.reset_input_buffer()

    if run_iso_9141_handshake(ser):
        # Если ISO 9141 успешно инициализирован, запрашиваем обороты
        # В ISO 9141 запросы проще: [Mode] [PID] [Checksum]
        # Запрос RPM (01 0C)
        req = [0x01, 0x0C]
        cs = sum(req) & 0xFF
        full_req = bytes(req + [cs])

        print(f"[>] Запрос данных: {full_req.hex()}")
        ser.write(full_req)

        # Читаем ответ (с учетом эха)
        res = ser.read(10)
        print(f"[<] Ответ ЭБУ: {res.hex()}")
    else:
        print("[-] ISO 9141 не ответил. Возможно, авто использует KWP2000 (Fast Init).")
        # Тут можно вызвать функцию Fast Init из предыдущего ответа

    ser.close()


if __name__ == "__main__":
    main()