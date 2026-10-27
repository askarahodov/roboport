import React from 'react';
import { Button, FormField } from '../../ui';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Assignment = { assignmentId: string; userId: string };

async function mutate(path: string, body: object, idempotencyKey?: string): Promise<unknown> {
  const csrf = await fetch('/api/v1/auth/csrf', {
    method: 'POST', credentials: 'include', cache: 'no-store',
  });
  if (!csrf.ok) throw Error('CSRF');
  const token: unknown = await csrf.json();
  if (!token || typeof token !== 'object' || !('csrfToken' in token)
      || typeof token.csrfToken !== 'string' || !token.csrfToken) throw Error('CSRF');
  const response = await fetch(path, {
    method: 'POST', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token.csrfToken,
      ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}) },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw Error('HTTP ' + response.status);
  return await response.json() as unknown;
}
function isAssignment(value: unknown): value is Assignment {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return typeof item.assignmentId === 'string' && uuid.test(item.assignmentId)
    && typeof item.userId === 'string' && uuid.test(item.userId);
}
function urlFor(vehicle: string) {
  return '/api/v1/dev/auto/vehicles/' + encodeURIComponent(vehicle.trim()) + '/intake-assignments';
}

/** Узкий dev-интерфейс: уже назначенный MANAGER делегирует только INTAKE, не MANAGER. */
export function ManagerIntakeAssignments() {
  const [vehicle, setVehicle] = React.useState('');
  const [staff, setStaff] = React.useState('');
  const [items, setItems] = React.useState<Assignment[]>([]);
  const grantKey = React.useRef<{ payload: string; key: string } | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [notice, setNotice] = React.useState<{ ok: boolean; message: string } | null>(null);

  function changedVehicle(value: string) {
    setVehicle(value);
    grantKey.current = null;
    setItems([]); // Сменили объект — нельзя оставлять назначения предыдущего автомобиля.
    setNotice(null);
  }

  async function load() {
    if (!uuid.test(vehicle.trim()) || busy) return;
    setBusy(true);
    setItems([]);
    setNotice(null);
    try {
      const response = await fetch(urlFor(vehicle), {
        credentials: 'include', cache: 'no-store', headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw Error('denied');
      const body: unknown = await response.json();
      if (!body || typeof body !== 'object' || !('items' in body)
          || !Array.isArray(body.items) || !body.items.every(isAssignment)) throw Error('invalid');
      setItems(body.items);
      setNotice({ ok: true, message: 'Список активных назначений обновлён.' });
    } catch {
      setItems([]);
      setNotice({ ok: false, message: 'Не удалось получить назначения: проверьте права на конкретную машину.' });
    } finally { setBusy(false); }
  }

  async function grant(event: React.FormEvent) {
    event.preventDefault();
    if (!uuid.test(vehicle.trim()) || !uuid.test(staff.trim()) || busy) return;
    setBusy(true);
    setItems([]);
    setNotice(null);
    const fingerprint = vehicle.trim().toLowerCase() + ':' + staff.trim().toLowerCase();
    if (grantKey.current?.payload !== fingerprint) {
      grantKey.current = { payload: fingerprint, key: crypto.randomUUID() };
    }
    const key = grantKey.current.key;
    try {
      const response: unknown = await mutate(urlFor(vehicle), { userId: staff.trim() }, key);
      if (!response || typeof response !== 'object'
          || !('assignmentId' in response) || typeof response.assignmentId !== 'string'
          || !uuid.test(response.assignmentId)) throw Error('invalid');
      grantKey.current = null;
      setStaff('');
      setNotice({ ok: true, message: 'Назначена только роль INTAKE на этот автомобиль. Обновите список.' });
    } catch {
      setNotice({ ok: false, message: 'Назначение отклонено: проверьте роль INTAKE, объектный доступ и отсутствие дубля.' });
    } finally { setBusy(false); }
  }

  async function revoke(assignmentId: string) {
    if (!uuid.test(vehicle.trim()) || !uuid.test(assignmentId) || busy) return;
    setBusy(true);
    setItems([]);
    setNotice(null);
    try {
      const result: unknown = await mutate(urlFor(vehicle) + '/' + assignmentId + '/revoke', {});
      if (!result || typeof result !== 'object' || !('status' in result)
          || !['REVOKED', 'ALREADY_REVOKED'].includes(String(result.status)))
        throw Error('invalid');
      setNotice({ ok: true, message: 'Назначение отозвано; для просмотра действующих прав обновите список.' });
    } catch {
      setNotice({ ok: false, message: 'Отзыв не выполнен: прав на автомобиль недостаточно.' });
    } finally { setBusy(false); }
  }

  return <section className="auto-intake-delegation" aria-label="Назначения приёмщиков (dev)">
    <h4>Делегирование приёмщика (dev)</h4>
    <p>Действующий менеджер может назначить или отозвать только INTAKE
      на **уже назначенный ему автомобиль**. Полномочия MANAGER,
      филиалы и реальные сотрудники здесь не настраиваются.</p>
    <form onSubmit={e => { void grant(e); }} aria-label="Выдать доступ приёмщику">
      <FormField id="auto-delegation-vehicle" label="UUID автомобиля для назначения">
        <input id="auto-delegation-vehicle" autoComplete="off" required
          value={vehicle} onChange={e => changedVehicle(e.target.value)} />
      </FormField>
      <FormField id="auto-delegation-user" label="UUID сотрудника с ролью INTAKE">
        <input id="auto-delegation-user" autoComplete="off" required
          value={staff} onChange={e => setStaff(e.target.value)} />
      </FormField>
      <Button type="submit" disabled={busy || !uuid.test(vehicle.trim()) || !uuid.test(staff.trim())}>
        Назначить приёмщика
      </Button>
      <Button type="button" variant="secondary"
        disabled={busy || !uuid.test(vehicle.trim())} onClick={() => { void load(); }}>
        Обновить назначения
      </Button>
    </form>
    {items.length > 0 && <ul className="auto-assignment-list">
      {items.map(item => <li key={item.assignmentId}>
        <span>Сотрудник <code>{item.userId}</code></span>
        <Button type="button" variant="secondary" disabled={busy}
          onClick={() => { void revoke(item.assignmentId); }}>
          Отозвать назначение
        </Button>
      </li>)}
    </ul>}
    {notice && <p role={notice.ok ? 'status' : 'alert'}>{notice.message}</p>}
  </section>;
}
