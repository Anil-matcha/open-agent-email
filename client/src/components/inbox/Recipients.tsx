import type { Message } from "@/lib/types";
import { statusClasses } from "@/lib/inbox-api";

/** Visible To/Cc/Reply-To and, on the sender's own copy, Bcc. Older messages fall back to `recipient`. */
export function AddressLines({ message }: { message: Pick<Message, "recipient" | "to" | "cc" | "bcc" | "reply_to"> }) {
  const rows: [string, string[]][] = [
    ["To", message.to?.length ? message.to : [message.recipient]],
    ["Cc", message.cc ?? []],
    ["Bcc", message.bcc ?? []],
    ["Reply-To", message.reply_to ?? []],
  ];
  return (
    <>
      {rows.filter(([, addresses]) => addresses.length).map(([label, addresses]) => (
        <div key={label} className="flex justify-between gap-3">
          <span className="text-[#8b949e] shrink-0">{label}:</span>
          <span className="font-mono text-blue-400 text-right break-all">{addresses.join(", ")}</span>
        </div>
      ))}
    </>
  );
}

/** Per-recipient outcome of an outbound message. Shown only when there is more than one recipient or a problem. */
export function DeliveryTable({ message }: { message: Pick<Message, "deliveries"> }) {
  const deliveries = message.deliveries ?? [];
  const interesting = deliveries.length > 1 || deliveries.some((item) => !["accepted_by_relay", "delivered", "pending"].includes(item.status));
  if (!interesting) return null;
  return (
    <table className="mt-2 w-full text-[10px]">
      <caption className="text-left text-[#8b949e] mb-1">Delivery per recipient</caption>
      <tbody>
        {deliveries.map((item) => (
          <tr key={item.address} className="align-top border-t border-[#30363d]">
            <td className="py-1 pr-2 font-mono text-[#e6edf3] break-all">{item.address}</td>
            <td className="py-1 pr-2 uppercase text-[#8b949e]">{item.kind}</td>
            <td className="py-1 pr-2">
              <span className={`px-1.5 py-0.5 rounded border ${statusClasses(item.status)}`}>{item.status.replace(/_/g, " ")}</span>
              {item.provider_delivery_status && <span className="ml-1 text-[#8b949e]">provider: {item.provider_delivery_status}</span>}
            </td>
            <td className="py-1 text-[#8b949e]">{item.detail || ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
