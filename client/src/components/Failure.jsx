// Erreur d'un agent : le message, puis la fin de la sortie Python (souvent un paquet manquant ou un chemin).
export default function Failure({ message, log }) {
  return (
    <div className="failure">
      <p className="error">Le run s'est arrêté : {message}</p>
      {log?.length > 0 && (
        <>
          <p className="note">Détail renvoyé par l'agent (copiez-le pour le signaler) :</p>
          <pre className="log">{log.join("\n")}</pre>
        </>
      )}
    </div>
  );
}
