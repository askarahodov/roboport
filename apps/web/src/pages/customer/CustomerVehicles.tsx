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
const PAGE_SIZE = 20;
const MAX_OFFSET = 10000;

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

function isVehiclePage(value: unknown): value is { items: Vehicle[] } {
  if (!value || typeof value !== 'object' || !('items' in value)) return false;
  const items = value.items;
  return Array.isArray(items) && items.length <= PAGE_SIZE && items.every(isVehicle);
}

export function CustomerVehicles() {
  const [items, setItems] = React.useState<Vehicle[]>([]);
  const [status, setStatus] = React.useState<LoadState>('loading');
  const [offset, setOffset] = React.useState(0);
  const [nextOffset, setNextOffset] = React.useState<number | null>(null);
  const [pageLoading, setPageLoading] = React.useState(false);
  const [pageError, setPageError] = React.useState(false);
  const [reload, setReload] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    if (offset === 0) {
      setStatus('loading');
      setItems([]);
      setNextOffset(null);
    } else {
      setPageLoading(true);
    }
    setPageError(false);
    void (async () => {
      try {
        // Backend возвращает 200 + пустой список, когда customer link отозвана.
        // Перед расширением списка повторно запрашиваем ВСЕ видимые страницы:
        // иначе карточки с уже отозванным доступом остались бы на экране.
        const fresh: Vehicle[] = [];
        let following: number | null = null;
        for (let pageOffset = 0; pageOffset <= offset; pageOffset += PAGE_SIZE) {
          const response = await fetch(
            '/api/v1/dev/auto/vehicles?limit=' + PAGE_SIZE + '&offset=' + pageOffset,
            {
              credentials: 'include', cache: 'no-store', signal: controller.signal,
              headers: { Accept: 'application/json' },
            },
          );
          if (controller.signal.aborted) return;
          if ([401, 403, 404].includes(response.status)) {
            // Отзыв сессии/доступа очищает даже ранее загруженные страницы.
            setItems([]);
            setNextOffset(null);
            setStatus('denied');
            return;
          }
          if (!response.ok) throw new Error('Vehicle API unavailable');
          const data: unknown = await response.json();
          if (!isVehiclePage(data)) throw new Error('Invalid vehicle page');
          if (controller.signal.aborted) return;
          // Server-side фильтр по актуальной связи действует для каждой страницы.
          // Offset может пересекать элементы после конкурентных изменений.
          for (const item of data.items) {
            if (!fresh.some(existing => existing.id === item.id)) fresh.push(item);
          }
          following = data.items.length === PAGE_SIZE && pageOffset + PAGE_SIZE <= MAX_OFFSET
            ? pageOffset + PAGE_SIZE : null;
          if (data.items.length < PAGE_SIZE) break;
        }
        if (controller.signal.aborted) return;
        setItems(fresh);
        setNextOffset(following);
        setStatus('ready');
      } catch {
        if (!controller.signal.aborted) {
          if (offset === 0) setStatus('error');
          else setPageError(true);
        }
      } finally {
        if (!controller.signal.aborted) setPageLoading(false);
      }
    })();
    return () => controller.abort();
  }, [offset, reload]);

  function refresh() {
    // Запрос с нуля пересчитывает права и не оставляет устаревший кэш.
    setOffset(0);
    setReload(value => value + 1);
  }

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
      : <>
        <p role="status" aria-live="polite">Показано автомобилей: {items.length}</p>
        <ul className="account-records">{items.map(vehicle =>
          <li key={vehicle.id} className="account-record">
            <strong>{vehicle.make} {vehicle.model}</strong>
            {vehicle.variant && <span>Комплектация: {vehicle.variant}</span>}
            {vehicle.modelYear !== null && <span>Год выпуска: {vehicle.modelYear}</span>}
            <span>{vehicle.vinLast4 === null ? 'VIN не указан' : 'VIN ••••' + vehicle.vinLast4}</span>
          </li>)}</ul>
        {pageLoading && <p role="status">Загружается следующая страница…</p>}
        {pageError && <p role="alert">Не удалось загрузить следующую страницу.
          Уже полученные автомобили сохранены до обновления списка.</p>}
        {pageError && <button type="button" disabled={pageLoading}
          onClick={() => setReload(value => value + 1)}>Повторить загрузку страницы</button>}
        {nextOffset !== null && !pageError && <button type="button"
          disabled={pageLoading} onClick={() => setOffset(nextOffset)}>
          {pageLoading ? 'Загрузка…' : 'Показать ещё автомобили'}
        </button>}
      </>)}
    {status !== 'loading' && <button type="button"
      onClick={refresh}>Обновить автомобили</button>}
  </section>;
}
