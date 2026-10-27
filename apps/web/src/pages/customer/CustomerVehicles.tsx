import React from 'react';
import { Alert, EmptyState, Skeleton } from '../../ui';

type Vehicle = {
  id: string;
  make: string;
  model: string;
  variant: string | null;
  modelYear: number | null;
  vinLast4: string | null;
};
type LoadState = 'loading' | 'ready' | 'denied' | 'error';

function isVehicle(value: unknown): value is Vehicle {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return typeof v.id === 'string' && /^[0-9a-fA-F-]{36}$/.test(v.id)
    && typeof v.make === 'string' && v.make.trim().length > 0
    && typeof v.model === 'string' && v.model.trim().length > 0
    && (v.variant === null || typeof v.variant === 'string')
    && (v.modelYear === null || (typeof v.modelYear === 'number'
      && Number.isInteger(v.modelYear) && v.modelYear >= 1886 && v.modelYear <= 2100))
    && (v.vinLast4 === null || (typeof v.vinLast4 === 'string'
      && /^[A-HJ-NPR-Z0-9]{4}$/.test(v.vinLast4)));
}

export function CustomerVehicles() {
  const [items, setItems] = React.useState<Vehicle[]>([]);
  const [status, setStatus] = React.useState<LoadState>('loading');
  const [reload, setReload] = React.useState(0);
  React.useEffect(() => {
    const controller = new AbortController();
    setStatus('loading');
    setItems([]);
    void (async () => {
      try {
        const response = await fetch('/api/v1/dev/auto/vehicles?limit=20&offset=0', {
          credentials: 'include', cache: 'no-store', signal: controller.signal,
          headers: { Accept: 'application/json' },
        });
        if (response.status === 401 || response.status === 403 || response.status === 404) {
          if (!controller.signal.aborted) setStatus('denied');
          return;
        }
        if (!response.ok) throw new Error('Vehicle API unavailable');
        const data: unknown = await response.json();
        if (!data || typeof data !== 'object' || !('items' in data)
          || !Array.isArray(data.items) || !data.items.every(isVehicle)) {
          throw new Error('Invalid vehicle list');
        }
        if (!controller.signal.aborted) {
          setItems(data.items as Vehicle[]);
          setStatus('ready');
        }
      } catch {
        if (!controller.signal.aborted) setStatus('error');
      }
    })();
    return () => controller.abort();
  }, [reload]);

  return <section aria-label="Автомобили в тестовом контуре">
    <p>Тестовый режим Airmatik: отображаются только подтверждённые и неотозванные
      связи. Добавление автомобиля через этот экран пока недоступно.</p>
    {status === 'loading' && <Skeleton label="Загрузка автомобилей…" />}
    {status === 'denied' && <Alert>Доступ к автомобилям не предоставлен или
      закрытый тестовый API выключен. Для проверки обратитесь к администратору стенда.</Alert>}
    {status === 'error' && <p role="alert">Не удалось получить список автомобилей.
      Данные не загружены.</p>}
    {status === 'ready' && (items.length === 0
      ? <EmptyState title="Подтверждённых автомобилей пока нет."
          description="Неподтверждённая заявка не даёт доступ к автомобилю. Проверка проводится сотрудником в тестовом контуре." />
      : <ul className="account-records">{items.map(vehicle =>
          <li key={vehicle.id} className="account-record">
            <strong>{vehicle.make} {vehicle.model}</strong>
            {vehicle.variant && <span>Комплектация: {vehicle.variant}</span>}
            {vehicle.modelYear !== null && <span>Год выпуска: {vehicle.modelYear}</span>}
            <span>{vehicle.vinLast4 === null ? 'VIN не указан' : 'VIN ••••' + vehicle.vinLast4}</span>
          </li>)}</ul>)}
    {status !== 'loading' && <button type="button"
      onClick={() => setReload(n => n + 1)}>Обновить автомобили</button>}
  </section>;
}
