import {request} from './client';import {Endmember} from '../types/domain';export const getEndmembers=(id:number)=>request<Endmember[]>(`/libraries/${id}/endmembers`);
