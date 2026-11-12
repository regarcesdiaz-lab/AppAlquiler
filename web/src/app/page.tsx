import styles from "./page.module.css";

export default function Home() {
  return (
    <main className={styles.main}>
      <p className={styles.brand}>AlquilerApp</p>
      <h1 className={styles.title}>Cobra a tiempo sin perseguir a nadie</h1>
      <p className={styles.lead}>
        Recordatorios por WhatsApp, pagos ordenados y recibos automáticos para tus apartamentos.
        Tú solo decides lo importante.
      </p>
      <p className={styles.soon}>Próximamente</p>
    </main>
  );
}
