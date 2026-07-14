export function StepIcon({ status }: { status: string }) {
  if (status === 'completed') return <span className="text-green-500 text-lg">&#10003;</span>
  if (status === 'running') return <span className="text-indigo-500 animate-spin inline-block">&#9696;</span>
  if (status === 'failed') return <span className="text-red-500 text-lg">&#10007;</span>
  return <span className="text-gray-300 text-lg">&#9675;</span>
}
