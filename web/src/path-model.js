export const VOXEL_PATHS='voxel_centers_v1';
export const CONTINUOUS_PATHS='continuous_columns_v1';
export const pathModelLabel=value=>({[VOXEL_PATHS]:'Voxel-center paths',[CONTINUOUS_PATHS]:'Continuous normal-incidence paths'}[value ?? VOXEL_PATHS] || `Unknown path model (${value})`);
export const savedPathModel=manifest=>manifest.acquisition?.path_model ?? manifest.request?.acquisition?.path_model ?? manifest.metadata?.path_model ?? manifest.estimate?.path_model ?? manifest.path_model ?? VOXEL_PATHS;
export const pathModelOptions=`<option value="${VOXEL_PATHS}">Voxel-center paths</option><option value="${CONTINUOUS_PATHS}">Continuous normal-incidence paths</option>`;
