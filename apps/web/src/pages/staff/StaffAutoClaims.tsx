import React from 'react';
import { Alert, Button, Card, FormField } from '../../ui';
import { ManagerIntakeAssignments } from './ManagerIntakeAssignments';

// Только синтетический loopback API. UUID клиента и машины сами по себе не дают полномочий.
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function validId(value: string) { return uuid.test(value.trim()); }
type Outcome = { type: 'ok' | 'error'; message: string };
class RateLimited extends Error {}

async function mutate(path: string, body?: Record<string, unknown>, idempotencyKey?: string): Promise<unknown> {
  const csrf = await fetch('/api/v1/auth/csrf', {
    method: 'POST', credentials: 'include', cache: 'no-store',
  });
  if (!csrf.ok) throw new Error('CSRF');
  const data: unknown = await csrf.json();
  if (!data || typeof data !== 'object' || !('csrfToken' in data)
    || typeof data.csrfToken !== 'string' || !data.csrfToken) throw new Error('CSRF');
  const result = await fetch(path, {
    method: 'POST', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': data.csrfToken,
      ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}) },
    body: JSON.stringify(body ?? {}),
  });
  if (result.status === 429) throw new RateLimited('Proof throttle');
  if (!result.ok) throw new Error('HTTP ' + result.status);
  return await result.json() as unknown;
}

function fields(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Некорректный ответ');
  return value as Record<string, unknown>;
}

/** Внешний UI не управляет назначениями: право INTAKE/MANAGER и object scope проверяет сервер. */
export function StaffAutoClaims({ roles }: { roles: string[] }) {
  const intake = roles.includes('INTAKE');
  const manager = roles.includes('MANAGER');
  const [vehicleId, setVehicleId] = React.useState('');
  const [customerId, setCustomerId] = React.useState('');
  const [linkId, setLinkId] = React.useState('');
  const [claimId, setClaimId] = React.useState('');
  const [revokeId, setRevokeId] = React.useState('');
  const [issued, setIssued] = React.useState<{ claimId: string; proof: string } | null>(null);
  const pendingKey = React.useRef<{ payload: string; key: string } | null>(null);
  const reviewKey = React.useRef<{ payload: string; key: string } | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [outcome, setOutcome] = React.useState<Outcome | null>(null);
  const [confirmRevoke, setConfirmRevoke] = React.useState(false);
  const [confirmReissue, setConfirmReissue] = React.useState(false);

  async function run(action: () => Promise<Outcome>) {
    if (busy) return;
    setBusy(true);
    setOutcome(null);
    // Одноразовый секрет никогда не переживает следующую операцию.
    setIssued(null);
    try { setOutcome(await action()); }
    catch (error) { setOutcome({ type: 'error',
      message: error instanceof RateLimited
        ? 'Лимит выдачи одноразовых кодов исчерпан. Повторите позже (до 60 минут).'
        : 'Операция не выполнена. Проверьте полномочия на автомобиль, сессию и состояние связи.' }); }
    finally { setBusy(false); }
  }

  function createLink() {
    if (!validId(vehicleId) || !validId(customerId)) return;
    const fingerprint = vehicleId.trim().toLowerCase() + ':' + customerId.trim().toLowerCase();
    if (pendingKey.current?.payload !== fingerprint) {
      pendingKey.current = { payload: fingerprint, key: crypto.randomUUID() };
    }
    const key = pendingKey.current.key;
    void run(async () => {
      const response = fields(await mutate(
        '/api/v1/dev/auto/vehicles/' + encodeURIComponent(vehicleId.trim()) + '/links',
        { customerId: customerId.trim() }, key,
      ));
      if (typeof response.linkId !== 'string' || !validId(response.linkId)
        || response.status !== 'PENDING') throw new Error('Bad link response');
      pendingKey.current = null;
      setLinkId(response.linkId);
      return { type: 'ok', message: 'Создана неподтверждённая связь. Доступ клиенту ещё не выдан.' };
    });
  }

  function issueProof() {
    if (!validId(linkId)) return;
    void run(async () => {
      const response = fields(await mutate(
        '/api/v1/dev/auto/links/' + encodeURIComponent(linkId.trim()) + '/issue-proof',
      ));
      if (typeof response.claimId !== 'string' || !validId(response.claimId)
        || typeof response.proof !== 'string' || response.proof.length < 40
        || response.proof.length > 200) throw new Error('Bad proof response');
      setIssued({ claimId: response.claimId, proof: response.proof });
      return { type: 'ok', message: 'Код выдан однократно. Передайте его по согласованному закрытому тестовому каналу.' };
    });
  }

  function reissueProof() {
    if (!validId(linkId) || !confirmReissue) return;
    void run(async () => {
      const response = fields(await mutate(
        '/api/v1/dev/auto/links/' + encodeURIComponent(linkId.trim()) + '/reissue-proof',
      ));
      if (response.status !== 'REISSUED'
        || typeof response.claimId !== 'string' || !validId(response.claimId)
        || typeof response.proof !== 'string' || response.proof.length < 40
        || response.proof.length > 200) throw new Error('Bad replacement response');
      setIssued({ claimId: response.claimId, proof: response.proof });
      setConfirmReissue(false);
      return { type: 'ok',
        message: 'Новый код создан. Предыдущий код аннулирован. Другой перевыпуск для этой заявки запрещён.' };
    });
  }

  function review(approve: boolean) {
    if (!validId(claimId)) return;
    const fingerprint = claimId.trim().toLowerCase() + ':' + String(approve);
    if (reviewKey.current?.payload !== fingerprint) {
      reviewKey.current = { payload: fingerprint, key: crypto.randomUUID() };
    }
    const key = reviewKey.current.key;
    void run(async () => {
      const response = fields(await mutate(
        '/api/v1/dev/auto/claims/' + encodeURIComponent(claimId.trim()) + '/review',
        { approve }, key,
      ));
      if (response.status !== (approve ? 'VERIFIED' : 'DECLINED'))
        throw new Error('Bad review response');
      reviewKey.current = null;
      setClaimId('');
      return { type: 'ok', message: approve
        ? 'Связь подтверждена независимым сотрудником.'
        : 'Подтверждение отклонено. Доступ к автомобилю не выдан.' };
    });
  }

  function revoke() {
    if (!validId(revokeId) || !confirmRevoke) return;
    void run(async () => {
      const response = fields(await mutate(
        '/api/v1/dev/auto/links/' + encodeURIComponent(revokeId.trim()) + '/revoke',
      ));
      if (response.status !== 'REVOKED') throw new Error('Bad revoke response');
      setRevokeId('');
      setConfirmRevoke(false);
      return { type: 'ok', message: 'Связь отозвана. Доступ к автомобилю прекращён.' };
    });
  }

  return <Card>
    <section className="auto-claim-panel" aria-label="Автомобильные привязки (dev)">
      <h3>Привязка автомобиля (dev)</h3>
      <p>Только синтетические данные и HTTPS loopback. Сервер дополнительно проверяет
        действующую роль и назначение на конкретный автомобиль. Владение не
        подтверждается автоматически после создания связи.</p>
      {intake && <div className="auto-claim-forms">
        <form onSubmit={e => { e.preventDefault(); createLink(); }}
          aria-label="Создание ожидающей связи">
          <h4>1. Предложить связь автомобиля и клиента</h4>
          <FormField label="UUID автомобиля" id="auto-vehicle-id">
            <input id="auto-vehicle-id" autoComplete="off" required
              value={vehicleId} onChange={e => setVehicleId(e.target.value)} />
          </FormField>
          <FormField label="UUID клиента" id="auto-customer-id">
            <input id="auto-customer-id" autoComplete="off" required
              value={customerId} onChange={e => setCustomerId(e.target.value)} />
          </FormField>
          <Button type="submit" disabled={busy || !validId(vehicleId) || !validId(customerId)}>
            Создать ожидающую связь
          </Button>
        </form>
        <form onSubmit={e => { e.preventDefault(); issueProof(); }}
          aria-label="Одноразовое доказательство связи">
          <h4>2. Выдать одноразовый код</h4>
          <FormField label="UUID связи" id="auto-link-id">
            <input id="auto-link-id" required autoComplete="off" value={linkId}
              onChange={e => { setLinkId(e.target.value); setIssued(null); setConfirmReissue(false); }} />
          </FormField>
          <Button type="submit" disabled={busy || !validId(linkId)}>
            Выдать код для клиента
          </Button>
          <p>Если первый ответ с кодом потерялся, его нельзя получить повторно.
            Возможен только один перевыпуск для этой связи, до любой попытки клиента.
            После перевыпуска прежний код навсегда недействителен.</p>
          <label className="auto-claim-confirm">
            <input type="checkbox" checked={confirmReissue}
              onChange={e => setConfirmReissue(e.target.checked)} />
            Подтверждаю замену и аннулирование предыдущего кода
          </label>
          <Button type="button" variant="secondary"
            disabled={busy || !validId(linkId) || !confirmReissue}
            onClick={reissueProof}>Перевыпустить код однократно</Button>
          {issued && <div className="auto-proof" role="status">
            <p><strong>Временный код — не копируйте его в задачи или логи.</strong></p>
            <p>UUID подтверждения: <code>{issued.claimId}</code></p>
            <p>Одноразовый код: <code>{issued.proof}</code></p>
            <Button type="button" variant="secondary" onClick={() => setIssued(null)}>Скрыть код</Button>
          </div>}
        </form>
      </div>}
      {manager && <div className="auto-claim-forms">
        <form onSubmit={e => { e.preventDefault(); review(true); }}
          aria-label="Независимое решение по автомобилю">
          <h4>3. Независимая проверка менеджером</h4>
          <p>Клиент должен предъявить код; только после этого возможен review.
            Решение нельзя принять тем же пользователем, который выдал код.</p>
          <FormField label="UUID подтверждения" id="auto-claim-id">
            <input id="auto-claim-id" required autoComplete="off"
              value={claimId} onChange={e => setClaimId(e.target.value)} />
          </FormField>
          <Button type="submit" disabled={busy || !validId(claimId)}>Подтвердить связь</Button>{' '}
          <Button type="button" variant="secondary" disabled={busy || !validId(claimId)}
            onClick={() => review(false)}>Отклонить связь</Button>
        </form>
        <form onSubmit={e => { e.preventDefault(); revoke(); }}
          aria-label="Отзыв подтверждённой связи">
          <h4>4. Отозвать связь</h4>
          <FormField label="UUID связи для отзыва" id="auto-revoke-id">
            <input id="auto-revoke-id" autoComplete="off" required
              value={revokeId} onChange={e => { setRevokeId(e.target.value); setConfirmRevoke(false); }} />
          </FormField>
          <label className="auto-claim-confirm"><input type="checkbox" checked={confirmRevoke}
            onChange={e => setConfirmRevoke(e.target.checked)} />
            Подтверждаю отзыв тестовой связи</label>
          <Button type="submit" disabled={busy || !validId(revokeId) || !confirmRevoke}>
            Отозвать доступ
          </Button>
        </form>
      </div>}
      {manager && <ManagerIntakeAssignments />}
      {outcome && <p role={outcome.type === 'error' ? 'alert' : 'status'}>{outcome.message}</p>}
    </section>
  </Card>;
}
