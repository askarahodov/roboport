import React from 'react';
import {Alert,Badge,Button,Card,Dialog,EmptyState,FormField,PageShell,Skeleton} from '../ui';

/** Static acceptance gallery: never requests or displays customer data. */
export function AutoUiGallery(){
  const [open,setOpen]=React.useState(false);
  const [message,setMessage]=React.useState('');
  return <PageShell>
    <h1>БоксРитм — проверка компонентов (dev)</h1>
    <p>Синтетические состояния интерфейса; не реальные заказы, оплаты или права доступа.</p>
    <section aria-label="Действия и кнопки"><h2>Действия и кнопки</h2>
      <Card><div className="auto-claim-forms">
        <Button onClick={()=>setOpen(true)}>Открыть диалог</Button>
        <Button variant="secondary" onClick={()=>setMessage('Тестовое действие выполнено')}>Вторичное действие</Button>
        <Button variant="ghost" onClick={()=>setMessage('Дополнительное действие')}>Дополнительное действие</Button>
        <Button disabled>Недоступное действие</Button>
      </div>{message&&<p role="status">{message}</p>}</Card>
    </section>
    <section aria-label="Статусы и ошибки"><h2>Статусы и ошибки</h2>
      <Card><div className="auto-claim-forms">
        <Badge tone="neutral">Ожидание</Badge>
        <Badge tone="success">Проверено</Badge>
        <Badge tone="warning">Согласование</Badge>
        <Badge tone="error">QC FAIL</Badge>
      </div>
        <Alert tone="info">Загрузка тестовых сведений: данные не отправляются.</Alert>
        <Alert tone="warning">409 — состояние изменилось. Обновите данные.</Alert>
        <Alert>403 — нет доступа. Не подставляйте чужие идентификаторы.</Alert>
        <EmptyState title="Ничего не найдено" description="404 или пустой список не раскрывают чужие записи."/>
        <Skeleton label="Синтетическая загрузка…"/>
      </Card>
    </section>
    <section aria-label="Формы"><h2>Форма и доступность</h2>
      <Card>
        <FormField id="gallery-comment" label="Тестовый комментарий" hint="Не вводите реальные персональные данные.">
          <input id="gallery-comment" value={message} onChange={e=>setMessage(e.target.value)} maxLength={80}/>
        </FormField>
        <FormField id="gallery-state" label="Тестовый статус">
          <select id="gallery-state" defaultValue="waiting">
            <option value="waiting">Ожидает</option><option value="approved">Согласовано</option>
          </select>
        </FormField>
      </Card>
    </section>
    <Dialog title="Пример диалога" open={open} onClose={()=>setOpen(false)}>
      <p>Синтетический пример подтверждения. Данные не изменяются.</p>
      <Button onClick={()=>setOpen(false)}>Закрыть диалог</Button>
    </Dialog>
  </PageShell>;
}
