// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { MetricQueryResult } from '@/types/governedMetrics';

/** Driver metadata also survives empty results and older PostgreSQL history. */
export const ResultColumnDetails = ({
	columns,
}: {
	columns: MetricQueryResult['column_metadata'];
}) => {
	if (!columns?.length) return null;
	return (
		<details>
			<summary className="cursor-pointer text-xs font-medium">
				Result columns and numeric encoding
			</summary>
			<div className="mt-2 overflow-x-auto">
				<table className="w-full text-left text-xs">
					<thead>
						<tr>
							<th className="p-2">Column</th>
							<th className="p-2">Database type</th>
							<th className="p-2">JSON encoding</th>
						</tr>
					</thead>
					<tbody>
						{columns.map((column) => (
							<tr key={column.name}>
								<td className="p-2">{column.name}</td>
								<td className="p-2">
									{column.dialect === 'mysql'
										? `MySQL type ${column.mysql_type_code ?? 'unknown'}`
										: `PostgreSQL OID ${column.postgres_type_oid ?? 'unknown'}`}
								</td>
								<td className="p-2">{column.json_encoding}</td>
							</tr>
						))}
					</tbody>
				</table>
			</div>
		</details>
	);
};
