import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'python'))
import aquisicao
import servidor
from test_servidor import connected, packet, result, serial


class DiagnosticoEstadosTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.state = servidor.DashboardState(clock=lambda: self.now)
        connected(self.state)
        serial(self.state, result())

    def test_continuous_result_is_not_capture_and_two_second_heartbeat_keeps_states(self):
        self.assertEqual(self.state.snapshot()['capture']['phase'], 'monitorando')
        self.now += 2
        connected(self.state)
        serial(self.state, packet(estados=[1, 0]))
        snap = self.state.snapshot()
        self.assertEqual(snap['states'], [1, 0])
        self.assertIsNotNone(snap['result'])
        self.assertIsNone(snap['last_state_loss'])

    def test_capture_result_keeps_receiving_phase(self):
        serial(self.state, result(modo='aquisicao', captura_ativa=True,
                                  captura_id='1234:1', entrada_a_seguir=True))
        self.assertEqual(self.state.snapshot()['capture']['phase'], 'recebendo')

    def test_fault_reason_survives_next_valid_result(self):
        serial(self.state, packet('falha', motivo='janela_ou_sensores_invalidos', estados=[-1, -1]))
        self.assertIsNone(self.state.snapshot()['result'])
        serial(self.state, dict(result(), estados=[-1, -1]))
        snap = self.state.snapshot()
        self.assertEqual(snap['states'], [-1, -1])
        self.assertIn('janela_ou_sensores_invalidos',snap['last_state_loss']['reason'])
        self.assertEqual(snap['last_state_loss']['previous_states'],[1,0])

    def test_boot_epoch_and_disconnect_are_distinguished(self):
        serial(self.state, packet(boot=99, estados=[-1,-1]))
        self.assertIn('ESP32 reiniciou',self.state.snapshot()['last_state_loss']['reason'])
        serial(self.state, dict(result(),boot=99))
        serial(self.state, packet(boot=99, epoch=2, estados=[-1,-1]))
        self.assertIn('epoch mudou',self.state.snapshot()['last_state_loss']['reason'])
        serial(self.state, dict(result(),boot=99,epoch=2))
        self.state.accept_event(dict(type='connection',connected=False,message='USB desconectado'))
        self.assertIn('USB desconectado',self.state.snapshot()['last_state_loss']['reason'])

    def test_serial_log_records_state_loss_before_http(self):
        before = result()
        after = packet('falha',estados=[-1,-1],motivo='janela_ou_sensores_invalidos')
        with self.assertLogs(level='INFO') as log:
            aquisicao.registrar_diagnostico_placa(after,before)
        data=json.loads(log.output[0].split('DIAGNOSTICO_PLACA ',1)[1])
        self.assertIn('mudanca_estados',data['gatilhos'])
        self.assertIn('falha_informada',data['gatilhos'])
        self.assertEqual(data['anterior']['estados'],[1,0])
        self.assertEqual(data['atual']['motivo'],'janela_ou_sensores_invalidos')

    def test_detailed_firmware_failure_reaches_dashboard_and_log(self):
        detail=dict(motivo='saturacao_adc_corrente',janela_base_zero=7,
                    medido=11,limite_ou_esperado=10,saturadas_i=11,saturadas_v=0)
        after=packet('falha',estados=[-1,-1],motivo='saturacao_adc_corrente',
                     diagnostico_entrada=detail,revisao_firmware='diagnostico_entrada_v1_20261023')
        serial(self.state,after)
        loss=self.state.snapshot()['last_state_loss']
        self.assertIn('medido: 11',loss['reason'])
        self.assertEqual(loss['message']['diagnostico_entrada'],detail)
        with self.assertLogs(level='INFO') as log:
            aquisicao.registrar_diagnostico_placa(after,result())
        data=json.loads(log.output[0].split('DIAGNOSTICO_PLACA ',1)[1])
        self.assertEqual(data['atual']['diagnostico_entrada'],detail)


if __name__=='__main__':
    unittest.main()
