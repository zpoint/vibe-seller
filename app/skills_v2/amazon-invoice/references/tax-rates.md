# Built-in tax rules by country

`generate_invoice.py` looks these up from the JSON's `country` field. They
are listed here so you can sanity-check the PDF against the order page.

| Country | Code | Tax | Rate | Prices on Order Page |
|---------|------|-----|------|---------------------|
| Saudi Arabia | SA | VAT | 15% | Include tax |
| UAE | AE | VAT | 5% | Include tax |
| Egypt | EG | VAT | 14% | Include tax |
| South Africa | ZA | VAT | 15% | Include tax |
| Turkey | TR | KDV | 20% | Include tax |
| UK | UK/GB | VAT | 20% | Include tax |
| Germany | DE | MwSt | 19% | Include tax |
| France | FR | TVA | 20% | Include tax |
| Italy | IT | IVA | 22% | Include tax |
| Spain | ES | IVA | 21% | Include tax |
| Netherlands | NL | BTW | 21% | Include tax |
| Poland | PL | VAT | 23% | Include tax |
| Sweden | SE | Moms | 25% | Include tax |
| Belgium | BE | BTW | 21% | Include tax |
| Ireland | IE | VAT | 23% | Include tax |
| Japan | JP | CT | 10% | Include tax |
| India | IN | GST | 18% | Include tax |
| Singapore | SG | GST | 9% | Include tax |
| Australia | AU | GST | 10% | Include tax |
| US | US | — | 0% | No tax |
| Canada | CA | GST | 5% | Exclude tax |
| Mexico | MX | IVA | 16% | Exclude tax |
| Brazil | BR | ICMS | 17% | Include tax |

**"Include tax"** means the prices shown on the order detail page already contain tax. The script back-calculates: `tax = gross × rate / (1 + rate)`.

**"Exclude tax"** means prices are pre-tax. The script adds tax on top: `tax = subtotal × rate`.
