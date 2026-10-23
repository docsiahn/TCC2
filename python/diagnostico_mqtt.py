"""Verifica broker e entrega MQTT usando somente a biblioteca padrao do Python."""
import argparse
import json
import socket
import uuid
from servidor import probe_mqtt


def string(value):
    data = value.encode('utf-8')
    return len(data).to_bytes(2, 'big') + data


def send(sock, header, body):
    size = len(body)
    encoded = bytearray()
    while True:
        n = size % 128
        size //= 128
        encoded.append(n | (128 if size else 0))
        if not size:
            break
    sock.sendall(bytes([header]) + encoded + body)


def receive(sock):
    def exact(n):
        data = b''
        while len(data) < n:
            part = sock.recv(n - len(data))
            if not part:
                raise RuntimeError('O broker encerrou a conexao.')
            data += part
        return data
    header = exact(1)[0]
    size, multiplier = 0, 1
    for i in range(4):
        value = exact(1)[0]
        size += (value & 127)*multiplier
        if not value & 128:
            break
        multiplier *= 128
    else:
        raise RuntimeError('Pacote MQTT invalido.')
    if size > 65536:
        raise RuntimeError('Pacote excessivo no diagnostico.')
    return header, exact(size)


def roundtrip(host, port):
    token = uuid.uuid4().hex
    topic = 'tcc/diagnostico/' + token
    payload = json.dumps({'diagnostico': True, 'token': token}).encode()
    with socket.create_connection((host, port), 3) as sock:
        sock.settimeout(3)
        send(sock, 0x10, b'\x00\x04MQTT\x04\x02\x00\x0a' + string('tcc_diag_' + token[:12]))
        if receive(sock) != (0x20, b'\x00\x00'):
            raise RuntimeError('Broker recusou MQTT 3.1.1 sem autenticacao.')
        send(sock, 0x82, b'\x00\x01' + string(topic) + b'\x00')
        header, body = receive(sock)
        if header != 0x90 or body != b'\x00\x01\x00':
            raise RuntimeError('Broker nao confirmou a assinatura.')
        send(sock, 0x30, string(topic) + payload)
        header, body = receive(sock)
        if header >> 4 != 3 or body != string(topic)+payload:
            raise RuntimeError('A mensagem publicada nao retornou intacta.')
        send(sock, 0xe0, b'')
    return {'host': host, 'porta': port, 'mqtt': 'OK', 'publicacao_e_assinatura': 'OK',
            'topico_temporario': topic, 'mensagem_retida': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--porta', default=1883, type=int)
    args = parser.parse_args()
    try:
        if not probe_mqtt(args.host, args.porta):
            raise RuntimeError('Nenhum broker escutando nesse endereco.')
        print(json.dumps(roundtrip(args.host, args.porta), indent=2))
    except (OSError, RuntimeError) as exc:
        parser.exit(1, 'MQTT: %s\n' % exc)
