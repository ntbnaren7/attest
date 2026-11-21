import { usePrefs } from '../prefs'
import { useExit } from '../motion'
import { NewCustomers } from './NewCustomers'

export function AddCustomer({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t } = usePrefs()
  const [closing, close] = useExit(onClose)
  return (
    <div className="dialog-wrap">
      <div className={`overlay ${closing ? 'closing' : ''}`} onClick={close} />
      <div className={`dialog wide ${closing ? 'closing' : ''}`} role="dialog" aria-modal="true" aria-labelledby="addc-title">
        <h2 id="addc-title">{t.addCustomer}</h2>
        <p className="intro">{t.addCustomerHint}</p>
        <NewCustomers initial={[]} source="hand" onAdded={onDone} />
        <div className="btn-row" style={{ marginTop: 16 }}>
          <button className="btn secondary" onClick={close}>{t.doneWord}</button>
        </div>
      </div>
    </div>
  )
}
