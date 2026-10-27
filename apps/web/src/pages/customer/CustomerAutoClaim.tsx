import React from 'react';
import { Button, FormField } from '../../ui';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Клиент может только предъявить код; подтвердить себе права он не может. */
export function CustomerAutoClaim() {
  const [claimId, setClaimId] = React.useState('');
  const [proof, setProof] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [message, setMessage] = React.useState<{ text: string; failed: boolean } | null>(null);

  async function redeem(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !uuid.test(claimId.trim()) || proof.length < 20 || proof.length > 200) return;
    setBusy(true);
    setMessage(null);
    // После отправки доказательство не остаётся в HTML input, URL или storage.
    const suppliedProof = proof;
    setProof('');
    try {
      const csrf = await fetch('/api/v1/auth/csrf', {
        method: 'POST', credentials: 'include', cache: 'no-store',
      });
      if (!csrf.ok) throw new Error('CSRF');
      const response: unknown = await csrf.json();
      if (!response || typeof response !== 'object'
        || !('csrfToken' in response) || typeof response.csrfToken !== 'string'
        || !response.csrfToken) throw new Error('Invalid CSRF');

      const result = await fetch(
        '/api/v1/dev/auto/claims/' + encodeURIComponent(claimId.trim()) + '/redeem',
        {
          method: 'POST', credentials: 'include', cache: 'no-store',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': response.csrfToken },
          body: JSON.stringify({ proof: suppliedProof }),
        },
      );
      if (!result.ok) throw new Error('denied');
      const data: unknown = await result.json();
      if (!data || typeof data !== 'object' || !('status' in data)
        || !['PENDING_REVIEW', 'REJECTED'].includes(String(data.status))) {
        throw new Error('Invalid response');
      }
      if (data.status === 'PENDING_REVIEW') {
        setClaimId('');
        setMessage({ failed: false,
          text: 'Код принят. Автомобиль появится только после независимого решения менеджера.' });
      } else {
        setMessage({ failed: true,
          text: 'Код не принят. Проверьте данные с сотрудником тестового стенда.' });
      }
    } catch {
      setMessage({ failed: true,
        text: 'Предъявление не удалось. Проверьте сессию, код и доступ к тестовому контуру.' });
    } finally {
      setBusy(false);
    }
  }

  return <form onSubmit={e => { void redeem(e); }} aria-label="Подтверждение автомобиля (dev)"
    className="auto-customer-claim">
    <h3>Ожидающая привязка автомобиля (dev)</h3>
    <p>Приёмщик выдаёт код только для синтетической машины. После ввода
      код проходит отдельную проверку менеджером: клиент не может
      подтвердить владение самостоятельно.</p>
    <FormField label="UUID подтверждения" id="auto-client-claim-id">
      <input id="auto-client-claim-id" required autoComplete="off" value={claimId}
        onChange={e => setClaimId(e.target.value)} />
    </FormField>
    <FormField label="Одноразовый код" id="auto-client-proof">
      <input id="auto-client-proof" type="password" required autoComplete="off"
        value={proof} onChange={e => setProof(e.target.value)} />
    </FormField>
    <Button type="submit" disabled={busy || !uuid.test(claimId.trim())
      || proof.length < 20 || proof.length > 200}>
      {busy ? 'Проверка…' : 'Предъявить код'}
    </Button>
    {message && <p role={message.failed ? 'alert' : 'status'}>{message.text}</p>}
  </form>;
}
